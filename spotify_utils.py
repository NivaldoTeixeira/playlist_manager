import logging
import re
from typing import Optional

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from config import (
    SPOTIPY_CLIENT_ID,
    SPOTIPY_CLIENT_SECRET,
    SPOTIPY_REDIRECT_URI,
    SCOPES,
    SPOTIFY_REFRESH_TOKEN,
)
from setlist_utils import Show, Song

logger = logging.getLogger("playlist-bot")

# Sufixos que a setlist.fm costuma trazer no nome e que derrubam a busca exata.
_RUIDO = re.compile(
    r"""\s*(?:
        [-–—]\s*(?:live|ao\s+vivo|acoustic|ac[úu]stico|remaster(?:ed)?|
                   single|radio\s+edit|demo)\b.*
      | \((?:[^()]*\b(?:live|ao\s+vivo|acoustic|ac[úu]stico|remaster(?:ed)?|
                       version|vers[ãa]o|edit|demo|mono|stereo)\b[^()]*)\)
      | \[(?:[^\[\]]*\b(?:live|remaster(?:ed)?|version|edit|demo)\b[^\[\]]*)\]
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Aspas tipográficas quebram a query do Spotify; normaliza para a versão simples.
_ASPAS = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


# ---------- SPOTIFY HELPERS ----------
def make_auth_manager() -> SpotifyOAuth:
    return SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope=SCOPES,
        show_dialog=False
    )

def get_spotify_client() -> spotipy.Spotify:
    if not SPOTIFY_REFRESH_TOKEN:
        raise RuntimeError("SPOTIFY_REFRESH_TOKEN não configurado. Use /login para gerar.")
    am = make_auth_manager()
    token_info = am.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
    access_token = token_info["access_token"]
    return spotipy.Spotify(auth=access_token)


# ---------- BUSCA DE FAIXAS ----------
def _limpar(nome: str) -> str:
    """Remove sufixos tipo '- Live' e '(Remastered)' que impedem o match exato."""
    return _RUIDO.sub("", nome.translate(_ASPAS)).strip(" -–—") or nome.strip()


def _consultas(song: Song) -> list[str]:
    """Consultas do mais específico ao mais tolerante, sem repetir."""
    nome, artista = song.name.translate(_ASPAS), song.search_artist
    limpo = _limpar(nome)

    brutas = [
        f'track:"{nome}" artist:"{artista}"',
        f'track:"{limpo}" artist:"{artista}"',
        # Sem aspas o Spotify tolera pontuação e grafia diferentes.
        f"{limpo} {artista}",
        limpo,
    ]

    vistas, saida = set(), []
    for q in brutas:
        if q not in vistas:
            vistas.add(q)
            saida.append(q)
    return saida


def _buscar_faixa(sp: spotipy.Spotify, song: Song) -> Optional[str]:
    """ID da faixa no Spotify, ou None se nenhuma consulta achou."""
    artista_alvo = song.search_artist.casefold()

    for q in _consultas(song):
        try:
            items = sp.search(q=q, limit=5, type="track").get("tracks", {}).get("items", [])
        except Exception:
            # Uma busca que falha não pode derrubar a playlist inteira.
            logger.exception("Busca falhou no Spotify: %s", q)
            continue
        if not items:
            continue

        # Entre os resultados, prefere um cujo artista bata com o esperado; as
        # consultas sem aspas são amplas e o primeiro resultado pode ser de outro
        # artista (cover, tributo, karaokê).
        for item in items:
            if any(a.get("name", "").casefold() == artista_alvo for a in item.get("artists", [])):
                return item["id"]
        return items[0]["id"]

    return None


# ---------- SPOTIFY: CRIAR PLAYLIST ----------
def create_playlist_with_songs(
    show: Show, playlist_name: Optional[str] = None
) -> tuple[Optional[str], int, list[str]]:
    """Cria a playlist do show no Spotify.

    Devolve (url, quantidade_adicionada, musicas_nao_encontradas). A url é None se
    nenhuma faixa foi encontrada — nesse caso nada é criado.
    """
    sp = get_spotify_client()

    track_ids: list[str] = []
    vistos: set[str] = set()
    faltando: list[str] = []

    for song in show.songs:
        tid = _buscar_faixa(sp, song)
        if not tid:
            faltando.append(song.name)
            continue
        # A mesma música pode aparecer duas vezes (bis, medley).
        if tid not in vistos:
            vistos.add(tid)
            track_ids.append(tid)

    if faltando:
        logger.info("Não achei no Spotify: %s", ", ".join(faltando))

    # Só cria a playlist se houver o que colocar dentro — antes, um show sem
    # nenhum match deixava uma playlist vazia na conta do usuário.
    if not track_ids:
        return None, 0, faltando

    me = sp.current_user()["id"]
    name = playlist_name or f"Setlist {show.artist}"
    descricao = f"{show.describe()} | By NT77" if show.describe() else "By NT77"
    playlist = sp.user_playlist_create(user=me, name=name, public=True, description=descricao)
    pid = playlist["id"]

    for i in range(0, len(track_ids), 100):
        sp.playlist_add_items(pid, track_ids[i:i+100])

    return playlist["external_urls"]["spotify"], len(track_ids), faltando
