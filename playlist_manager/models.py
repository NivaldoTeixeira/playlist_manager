"""O vocabulário comum do projeto: o que é um show e o que é uma música.

Fica fora de `integrations` de propósito. A setlist.fm produz estes objetos e o
Spotify os consome, mas nenhum dos dois precisa conhecer o outro para isso.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Song:
    name: str
    # Artista a usar na busca do Spotify. Em covers é o artista original, não a
    # banda do show — procurar "Helter Skelter" com artist:"Mötley Crüe" não acha.
    search_artist: str
    # Em quantos shows a música apareceu. Vale 1 para um show único e serve de
    # critério de desempate na ordenação da playlist.
    plays: int = 1


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
