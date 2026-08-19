"""OAuth do Spotify, busca das faixas e criação da playlist."""

import logging
import re
import unicodedata
from dataclasses import dataclass

import spotipy
from spotipy.cache_handler import MemoryCacheHandler
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
    """O gerenciador de OAuth, sem cache em disco.

    Por padrão o spotipy grava um arquivo `.cache` no diretório de trabalho a
    cada renovação de token. Isso atrapalhava de duas formas: o `/callback`
    devolvia o token guardado em vez de trocar o código novo — quebrando a
    recuperação de credencial vencida, que é justamente quando ele é usado — e
    deixava uma credencial viva em disco no servidor. Quem manda aqui é o
    SPOTIFY_REFRESH_TOKEN do ambiente, então o cache não tem função.
    """
    return SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope=SCOPES,
        show_dialog=False,
        requests_timeout=TIMEOUT,
        cache_handler=MemoryCacheHandler(),
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


def _normalizar_artista(nome: str) -> str:
    """Forma comparável de um nome de artista.

    A setlist.fm e o Spotify escrevem o mesmo artista de formas diferentes
    (acento, hífen, "&" no lugar de "and", "The" na frente). Comparar as duas
    grafias cruas descartaria a faixa certa e a música cairia como ausente.
    """
    base = unicodedata.normalize("NFKD", nome.casefold())
    base = "".join(c for c in base if not unicodedata.combining(c))
    partes = [p for p in re.split(r"[^a-z0-9]+", base) if p and p not in ("the", "and")]
    return " ".join(partes)


def _e_do_artista(item: dict, artista: str) -> bool:
    """A faixa é creditada ao artista procurado (inclusive como participação)?"""
    alvo = _normalizar_artista(artista)
    return any(_normalizar_artista(a.get("name", "")) == alvo for a in item.get("artists", []))


def _consultas(song: Song) -> list[tuple[str, tuple[str, ...]]]:
    """Consultas do mais específico ao mais tolerante, com os artistas que cada uma aceita.

    Em cover a banda do show vem primeiro e o artista original só depois: quem
    pediu a setlist de uma banda quer a versão dela, quando ela gravou uma.
    """
    # O nome do artista também é delimitado por aspas em artist:"...", então
    # precisa da mesma limpeza: sem ela um 'Weird Al' Yankovic ou um artista com
    # aspas tipográficas quebrava as duas consultas precisas.
    nome = song.name.translate(_ASPAS)
    limpo = _limpar(nome)
    artistas = song.search_artists

    brutas: list[tuple[str, tuple[str, ...]]] = []
    for artista in artistas:
        a = artista.translate(_ASPAS)
        brutas += [
            (f'track:"{nome}" artist:"{a}"', (artista,)),
            (f'track:"{limpo}" artist:"{a}"', (artista,)),
            # Sem aspas o Spotify tolera pontuação e grafia diferentes.
            (f"{limpo} {a}", (artista,)),
        ]
    # Último recurso: só o nome da música, para o caso de o artista estar escrito
    # de um jeito que a consulta com artist:"..." não casa. O filtro de artista
    # continua valendo em cima do resultado.
    brutas.append((limpo, artistas))

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

    Só devolve faixa creditada a um dos artistas esperados. A busca do Spotify é
    aproximada mesmo com artist:"...", então o primeiro resultado pode ser de
    outra banda — era assim que uma playlist de um artista ganhava a música de
    outro. Sem candidato do artista certo, a música entra como não encontrada,
    que é a verdade e o usuário vê na resposta.
    """
    rodou_alguma = False

    for q, aceitos in _consultas(song):
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

        # `aceitos` já vem na ordem de preferência (banda do show antes do
        # artista original do cover), e dentro dela vale a ordem do Spotify.
        for artista in aceitos:
            for item in items:
                if _e_do_artista(item, artista):
                    return item

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
    # Buscadas e ausentes do catálogo.
    faltando: list[str]
    # Buscas que nem chegaram a rodar. É outra coisa: dizer que estas "não estão
    # no Spotify" seria mentira, porque ninguém chegou a olhar. Ficam separadas
    # para o usuário saber que pode tentar de novo e conseguir mais faixas.
    nao_verificadas: list[str]
    consultadas: int


def _resolver_faixas(sp: spotipy.Spotify, songs: list[Song]) -> _Resultado:
    """Procura cada música no Spotify, uma vez só por música."""
    faixas: list[_Faixa] = []
    faltando: list[str] = []
    vistos: set[str] = set()
    # A mesma música pode aparecer duas vezes (bis, medley). Guardar o resultado
    # evita repetir a busca e evita listá-la duas vezes como não encontrada.
    resolvidas: dict[tuple[str, str], dict | None] = {}
    nao_verificadas: list[str] = []

    for song in songs:
        chave = (song.name, song.artist, song.cover_of)
        if chave in resolvidas:
            continue
        verificou = True
        try:
            item = _buscar_faixa(sp, song)
        except BuscaIndisponivel:
            verificou, item = False, None
        resolvidas[chave] = item

        if not verificou:
            nao_verificadas.append(song.name)
        elif item is None:
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

    return _Resultado(faixas, faltando, nao_verificadas, len(resolvidas))


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
) -> tuple[str | None, int, list[str], list[str]]:
    """Cria a playlist do show no Spotify.

    Devolve (url, quantidade_adicionada, nao_encontradas, nao_verificadas). A url
    é None se nenhuma faixa foi encontrada — nesse caso nada é criado.

    As duas listas são separadas de propósito: a primeira é repertório que o
    catálogo do Spotify não tem, a segunda é busca que falhou. Juntá-las diria ao
    usuário que a música não existe quando ninguém chegou a procurar.
    """
    sp = get_spotify_client()
    resultado = _resolver_faixas(sp, show.songs)

    # Se nada foi achado e houve falha de busca, o problema é o Spotify, não o
    # repertório: sobe o erro em vez de dizer que nenhuma música existe.
    if not resultado.faixas and resultado.nao_verificadas:
        raise SpotifyIndisponivel(
            f"Buscas no Spotify indisponíveis "
            f"({len(resultado.nao_verificadas)} de {resultado.consultadas})."
        )

    if resultado.faltando:
        logger.info("Não achei no Spotify: %s", ", ".join(resultado.faltando))
    if resultado.nao_verificadas:
        # Uma falha parcial ainda rende playlist, mas menor do que deveria. Sem
        # este log, a diferença ficava sem explicação nenhuma.
        logger.warning("Busca falhou, não pude verificar: %s",
                       ", ".join(resultado.nao_verificadas))

    # Só cria a playlist se houver o que colocar dentro — antes, um show sem
    # nenhum match deixava uma playlist vazia na conta do usuário.
    if not resultado.faixas:
        return None, 0, resultado.faltando, resultado.nao_verificadas

    track_ids = [f.track_id for f in _ordenar(resultado.faixas)]
    nome = playlist_name or f"Setlist {show.artist}"
    url = _criar_playlist(sp, nome, _descricao(show), track_ids)
    return url, len(track_ids), resultado.faltando, resultado.nao_verificadas
