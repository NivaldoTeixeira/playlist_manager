"""Orquestra um pedido de playlist, sem saber que existe Telegram.

É a regra do produto: o que é um pedido específico, qual show usar, como a
playlist se chama. Os handlers acima ficam só com o transporte — traduzir
mensagem em pedido e resultado em texto — e as integrações abaixo ficam só com
o serviço externo delas.

Este também é o único módulo que joga trabalho em thread. `openai`, `requests` e
`spotipy` são síncronos e um pedido leva dezenas de segundos (uma busca no
Spotify por música); chamá-los direto travaria o event loop, segurando o
`/health` e os webhooks seguintes — justamente o timeout do Telegram que a fila
de updates existe para evitar. Concentrar isso aqui evita que um chamador novo
esqueça o `to_thread` e derrube o serviço sem perceber.
"""

import asyncio
from dataclasses import dataclass

from playlist_manager.integrations.llm import parse_request
from playlist_manager.integrations.setlist_fm import (
    SHOWS_RECENTES,
    average_setlist,
    get_recent_shows,
    get_setlist,
)
from playlist_manager.integrations.spotify import create_playlist_with_songs
from playlist_manager.models import Show


@dataclass(frozen=True)
class Pedido:
    """O que o usuário pediu, já interpretado."""

    artist: str | None
    city: str | None = None
    year: str | None = None

    @property
    def entendido(self) -> bool:
        """Sem artista não há o que buscar — cidade e ano sozinhos não bastam."""
        return bool(self.artist)

    @property
    def especifico(self) -> bool:
        """Cidade ou ano apontam para um show; sem eles, o usuário escolhe."""
        return bool(self.city or self.year)


@dataclass(frozen=True)
class Playlist:
    """O resultado da montagem: o link e o que ficou de fora."""

    url: str | None
    adicionadas: int
    faltando: list[str]

    @property
    def criada(self) -> bool:
        """Sem nenhuma faixa encontrada nada é criado, para não deixar playlist vazia."""
        return self.url is not None


async def interpretar(texto: str) -> Pedido:
    """Extrai artista, cidade e ano do texto em linguagem natural."""
    artist, city, year = await asyncio.to_thread(parse_request, texto)
    return Pedido(artist=artist, city=city, year=year)


async def show_do_pedido(pedido: Pedido) -> Show | None:
    """O show que atende um pedido específico, ou None se não houver."""
    return await asyncio.to_thread(get_setlist, pedido.artist, pedido.city, pedido.year)


async def shows_recentes(artist: str, limite: int = SHOWS_RECENTES) -> list[Show]:
    """Os shows recentes a oferecer quando o pedido não diz cidade nem ano."""
    return await asyncio.to_thread(get_recent_shows, artist, None, None, limite)


def setlist_media(artist: str, shows: list[Show]) -> Show | None:
    """O repertório típico do artista como um show sintético, ou None sem repertório comum.

    Não vai para thread: a conta é feita sobre os shows já baixados, sem I/O.
    """
    songs = average_setlist(shows)
    return Show(artist=artist, songs=songs) if songs else None


async def criar_playlist(show: Show, nome: str) -> Playlist:
    """Cria a playlist no Spotify e devolve o link com as faixas não encontradas."""
    url, adicionadas, faltando = await asyncio.to_thread(create_playlist_with_songs, show, nome)
    return Playlist(url=url, adicionadas=adicionadas, faltando=faltando)


# ---------- nome da playlist ----------
# O nome é dado ao Spotify, não dito ao usuário, então mora aqui e não em messages.
def nome_do_pedido(pedido: Pedido) -> str:
    return " ".join(p for p in ("Setlist", pedido.artist, pedido.city, pedido.year) if p)


def nome_do_show(artist: str, show: Show) -> str:
    return " ".join(p for p in ("Setlist", artist, show.city, show.date) if p)


def nome_da_media(artist: str) -> str:
    return f"Setlist média {artist}"
