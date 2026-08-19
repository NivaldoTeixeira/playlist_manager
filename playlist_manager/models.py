"""O vocabulário comum do projeto: o que é um show e o que é uma música.

Fica fora de `integrations` de propósito. A setlist.fm produz estes objetos e o
Spotify os consome, mas nenhum dos dois precisa conhecer o outro para isso.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Song:
    name: str
    # Banda que tocou a música no show. É o primeiro artista procurado no
    # Spotify, inclusive em cover: se a banda gravou a própria versão, é ela que
    # pertence a uma playlist do show, não a gravação alheia.
    artist: str
    # Em quantos shows a música apareceu. Vale 1 para um show único e serve de
    # critério de desempate na ordenação da playlist.
    plays: int = 1
    # Artista original, quando a setlist.fm marca a música como cover. Serve de
    # alternativa: procurar "Helter Skelter" só com artist:"Mötley Crüe" não acha
    # nada se a banda nunca gravou o cover.
    cover_of: str | None = None

    @property
    def search_artists(self) -> tuple[str, ...]:
        """Artistas a procurar no Spotify, do preferido ao alternativo."""
        if self.cover_of and self.cover_of.casefold() != self.artist.casefold():
            return (self.artist, self.cover_of)
        return (self.artist,)


@dataclass(frozen=True)
class Show:
    artist: str
    songs: list[Song] = field(default_factory=list)
    venue: str | None = None
    city: str | None = None
    date: str | None = None
    url: str | None = None

    def describe(self) -> str:
        """Descrição curta do show, para o bot dizer qual setlist usou."""
        local = " - ".join(p for p in (self.venue, self.city) if p)
        partes = [p for p in (local, self.date) if p]
        return ", ".join(partes) if partes else self.artist
