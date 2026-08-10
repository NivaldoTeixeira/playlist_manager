"""OAuth do Spotify, busca das faixas e criação da playlist."""

import logging
import re
from dataclasses import dataclass

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from playlist_manager.config import (
    SCOPES,
    SPOTIFY_REFRESH_TOKEN,
    SPOTIPY_CLIENT_ID,
    SPOTIPY_CLIENT_SECRET,
    SPOTIPY_REDIRECT_URI,
)
from playlist_manager.errors import SpotifyIndisponivel
from playlist_manager.models import Show, Song

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

# Aspas duplas (retas ou tipográficas) são o delimitador de track:"..." — mantê-las
# no termo quebra a query e a música vira "não encontrada", como em “Heroes”.
# Apóstrofos são seguros dentro das aspas, só normaliza a forma tipográfica.
_ASPAS = str.maketrans({"“": "", "”": "", '"': "", "‘": "'", "’": "'"})


class BuscaIndisponivel(Exception):
    """Nenhuma consulta chegou a rodar — problema no Spotify, não música ausente.

    Fica aqui, e não em `errors`, porque nunca sai deste módulo: sinaliza uma
    música específica para `create_playlist_with_songs`, que decide se o caso
    vira SpotifyIndisponivel para o usuário.
    """


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
        raise SpotifyIndisponivel("SPOTIFY_REFRESH_TOKEN não configurado. Use /login para gerar.")
    am = make_auth_manager()
    try:
        token_info = am.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
    except Exception as e:
        # Refresh token revogado ou vencido cai aqui. É a falha mais provável
        # depois de um tempo sem uso, e sem essa distinção ela virava um
        # "deu erro" genérico que só o log explicava.
        logger.warning("Não consegui renovar o token do Spotify: %s", e)
        raise SpotifyIndisponivel(str(e)) from e
    return spotipy.Spotify(auth=token_info["access_token"])


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




def _e_sistemico(e: BaseException) -> bool:
    """Erro que vai afetar todas as buscas: credencial, cota ou Spotify fora."""
    status = getattr(e, "http_status", None)
    return isinstance(status, int) and (status in (401, 403, 429) or status >= 500)


def _buscar_faixa(sp: spotipy.Spotify, song: Song) -> dict | None:
    """A faixa encontrada no Spotify, ou None se as consultas não acharam nada.

    Devolve o item cru da API para o chamador aproveitar `popularity` além do id.
    Levanta BuscaIndisponivel quando nenhuma consulta chegou a rodar, para não
    reportar "não achei essa música" no que na verdade é falha do Spotify.
    """
    artista_alvo = song.search_artist.casefold()
    rodou_alguma = False

    for q in _consultas(song):
        try:
            items = sp.search(q=q, limit=5, type="track").get("tracks", {}).get("items", [])
        except Exception as e:
            # 401/403/429/5xx afetam toda a playlist: insistir nas próximas músicas
            # só queima cota e atrasa o erro real.
            if _e_sistemico(e):
                raise SpotifyIndisponivel(str(e)) from e
            logger.warning("Busca falhou no Spotify (%s): %s", q, e)
            continue
        rodou_alguma = True
        if not items:
            continue

        # Entre os resultados, prefere um cujo artista bata com o esperado; as
        # consultas sem aspas são amplas e o primeiro resultado pode ser de outro
        # artista (cover, tributo, karaokê).
        for item in items:
            if any(a.get("name", "").casefold() == artista_alvo for a in item.get("artists", [])):
                return item
        return items[0]

    if not rodou_alguma:
        raise BuscaIndisponivel(song.name)
    return None


# ---------- ORDENAÇÃO DA PLAYLIST ----------
@dataclass(frozen=True)
class _Faixa:
    track_id: str
    popularidade: int
    plays: int
    nome: str


def _ordenar(faixas: list[_Faixa]) -> list[_Faixa]:
    """Ordena a playlist sem seguir a ordem do show, que estraga a surpresa.

    Critério em cascata, do mais para o menos informativo:
    1. popularidade no Spotify, da mais tocada para a menos;
    2. quantas vezes a música apareceu nos shows considerados;
    3. ordem alfabética.

    A chave composta já produz essa cascata: sem popularidade todos empatam em 0
    e o número de aparições decide; num show único todos têm uma aparição e sobra
    o nome.
    """
    return sorted(
        faixas,
        key=lambda f: (-f.popularidade, -f.plays, f.nome.casefold()),
    )


# ---------- SPOTIFY: CRIAR PLAYLIST ----------
def create_playlist_with_songs(
    show: Show, playlist_name: str | None = None
) -> tuple[str | None, int, list[str]]:
    """Cria a playlist do show no Spotify.

    Devolve (url, quantidade_adicionada, musicas_nao_encontradas). A url é None se
    nenhuma faixa foi encontrada — nesse caso nada é criado.
    """
    sp = get_spotify_client()

    encontradas: list[_Faixa] = []
    vistos: set[str] = set()
    faltando: list[str] = []
    # A mesma música pode aparecer duas vezes (bis, medley). Guardar o resultado
    # evita repetir a busca e evita listá-la duas vezes como não encontrada.
    resolvidas: dict[tuple[str, str], dict | None] = {}
    indisponiveis = 0

    for song in show.songs:
        chave = (song.name, song.search_artist)
        if chave in resolvidas:
            continue
        try:
            item = _buscar_faixa(sp, song)
        except BuscaIndisponivel:
            indisponiveis += 1
            item = None
        resolvidas[chave] = item

        if not item:
            faltando.append(song.name)
        elif item["id"] not in vistos:
            vistos.add(item["id"])
            encontradas.append(_Faixa(
                track_id=item["id"],
                # Popularidade do Spotify: 0-100, pelo total de reproduções e o
                # quão recentes elas são. Ausente vira 0 e cai para o desempate.
                popularidade=item.get("popularity") or 0,
                plays=song.plays,
                nome=song.name,
            ))

    track_ids = [f.track_id for f in _ordenar(encontradas)]

    # Se nada foi achado e houve falha de busca, o problema é o Spotify, não o
    # repertório: sobe o erro em vez de dizer que nenhuma música existe.
    if not track_ids and indisponiveis:
        raise SpotifyIndisponivel(
            f"Buscas no Spotify indisponíveis ({indisponiveis} de {len(resolvidas)})."
        )

    if faltando:
        logger.info("Não achei no Spotify: %s", ", ".join(faltando))

    # Só cria a playlist se houver o que colocar dentro — antes, um show sem
    # nenhum match deixava uma playlist vazia na conta do usuário.
    if not track_ids:
        return None, 0, faltando

    name = playlist_name or f"Setlist {show.artist}"
    # describe() pode vir vazio num show sem artista nem local; sem a guarda a
    # descrição começaria com " | ".
    descricao = " | ".join(
        p for p in (show.describe(), "Sem spoiler: ordem por popularidade", "By NT77") if p
    )
    try:
        me = sp.current_user()["id"]
        playlist = sp.user_playlist_create(user=me, name=name, public=True, description=descricao)
        pid = playlist["id"]
        for i in range(0, len(track_ids), 100):
            sp.playlist_add_items(pid, track_ids[i:i+100])
    except Exception as e:
        # Escopo insuficiente ou app em modo de desenvolvimento (403) caem aqui.
        logger.warning("Falha ao criar a playlist: %s", e)
        raise SpotifyIndisponivel(str(e)) from e

    return playlist["external_urls"]["spotify"], len(track_ids), faltando
