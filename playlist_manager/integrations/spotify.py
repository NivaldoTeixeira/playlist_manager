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

# Segundos até desistir de cada chamada. O spotipy não impõe limite por padrão, e
# uma playlist grande faz uma busca por música: uma resposta pendurada seguraria
# a thread e o pedido inteiro junto.
TIMEOUT = 20

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
        show_dialog=False,
        requests_timeout=TIMEOUT,
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
    return spotipy.Spotify(auth=token_info["access_token"], requests_timeout=TIMEOUT)


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
    # dict preserva a ordem de inserção: tira as repetidas (comuns quando o nome
    # já vem limpo) sem perder a cascata do mais específico ao mais tolerante.
    return list(dict.fromkeys(brutas))


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
# A API aceita no máximo 100 faixas por chamada de adição.
LOTE_DE_FAIXAS = 100


@dataclass(frozen=True)
class _Resultado:
    """O que a varredura da setlist encontrou no Spotify."""

    faixas: list[_Faixa]
    faltando: list[str]
    # Músicas cuja busca nem chegou a rodar. Diferente de não encontrada: várias
    # delas significam Spotify fora, não repertório ausente do catálogo.
    indisponiveis: int
    consultadas: int


def _resolver_faixas(sp: spotipy.Spotify, songs: list[Song]) -> _Resultado:
    """Procura cada música no Spotify, uma vez só por música."""
    faixas: list[_Faixa] = []
    faltando: list[str] = []
    vistos: set[str] = set()
    # A mesma música pode aparecer duas vezes (bis, medley). Guardar o resultado
    # evita repetir a busca e evita listá-la duas vezes como não encontrada.
    resolvidas: dict[tuple[str, str], dict | None] = {}
    indisponiveis = 0

    for song in songs:
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
            faixas.append(_Faixa(
                track_id=item["id"],
                # Popularidade do Spotify: 0-100, pelo total de reproduções e o
                # quão recentes elas são. Ausente vira 0 e cai para o desempate.
                popularidade=item.get("popularity") or 0,
                plays=song.plays,
                nome=song.name,
            ))

    return _Resultado(faixas, faltando, indisponiveis, len(resolvidas))


def _descricao(show: Show) -> str:
    """Descrição da playlist. describe() pode vir vazio num show sem artista nem
    local, e sem a guarda a descrição começaria com ' | '."""
    partes = (show.describe(), "Sem spoiler: ordem por popularidade", "By NT77")
    return " | ".join(p for p in partes if p)


def _criar_playlist(sp: spotipy.Spotify, nome: str, descricao: str, track_ids: list[str]) -> str:
    """Cria a playlist com as faixas já na ordem final e devolve a URL."""
    try:
        me = sp.current_user()["id"]
        playlist = sp.user_playlist_create(user=me, name=nome, public=True, description=descricao)
        pid = playlist["id"]
        for i in range(0, len(track_ids), LOTE_DE_FAIXAS):
            sp.playlist_add_items(pid, track_ids[i:i + LOTE_DE_FAIXAS])
    except Exception as e:
        # Escopo insuficiente ou app em modo de desenvolvimento (403) caem aqui.
        logger.warning("Falha ao criar a playlist: %s", e)
        raise SpotifyIndisponivel(str(e)) from e

    return playlist["external_urls"]["spotify"]


def create_playlist_with_songs(
    show: Show, playlist_name: str | None = None
) -> tuple[str | None, int, list[str]]:
    """Cria a playlist do show no Spotify.

    Devolve (url, quantidade_adicionada, musicas_nao_encontradas). A url é None se
    nenhuma faixa foi encontrada — nesse caso nada é criado.
    """
    sp = get_spotify_client()
    resultado = _resolver_faixas(sp, show.songs)

    # Se nada foi achado e houve falha de busca, o problema é o Spotify, não o
    # repertório: sobe o erro em vez de dizer que nenhuma música existe.
    if not resultado.faixas and resultado.indisponiveis:
        raise SpotifyIndisponivel(
            f"Buscas no Spotify indisponíveis "
            f"({resultado.indisponiveis} de {resultado.consultadas})."
        )

    if resultado.faltando:
        logger.info("Não achei no Spotify: %s", ", ".join(resultado.faltando))

    # Só cria a playlist se houver o que colocar dentro — antes, um show sem
    # nenhum match deixava uma playlist vazia na conta do usuário.
    if not resultado.faixas:
        return None, 0, resultado.faltando

    track_ids = [f.track_id for f in _ordenar(resultado.faixas)]
    nome = playlist_name or f"Setlist {show.artist}"
    url = _criar_playlist(sp, nome, _descricao(show), track_ids)
    return url, len(track_ids), resultado.faltando
