import logging
from dataclasses import dataclass, field
from typing import Optional

import requests

from config import SETLIST_KEY

logger = logging.getLogger("playlist-bot")

API_URL = "https://api.setlist.fm/rest/1.0/search/setlists"


@dataclass(frozen=True)
class Song:
    name: str
    # Artista a usar na busca do Spotify. Em covers é o artista original, não a
    # banda do show — procurar "Helter Skelter" com artist:"Mötley Crüe" não acha.
    search_artist: str


@dataclass(frozen=True)
class Show:
    artist: str
    songs: list[Song] = field(default_factory=list)
    venue: Optional[str] = None
    city: Optional[str] = None
    date: Optional[str] = None
    url: Optional[str] = None

    def describe(self) -> str:
        """Descrição curta do show, para o bot dizer qual setlist usou."""
        local = " - ".join(p for p in (self.venue, self.city) if p)
        partes = [p for p in (local, self.date) if p]
        return ", ".join(partes) if partes else self.artist


def _parse_songs(setlist: dict, artista_do_show: str) -> list[Song]:
    songs: list[Song] = []
    for bloco in setlist.get("sets", {}).get("set", []):
        for song in bloco.get("song", []):
            nome = song.get("name")
            if not nome:
                continue
            # Cover traz o artista original; sem isso a busca no Spotify erra.
            cover = (song.get("cover") or {}).get("name")
            songs.append(Song(name=nome, search_artist=cover or artista_do_show))
    return songs


def _parse_show(setlist: dict) -> Show:
    venue = setlist.get("venue") or {}
    cidade = (venue.get("city") or {}).get("name")
    artista = (setlist.get("artist") or {}).get("name") or ""

    # A API devolve a data como dd-MM-yyyy.
    data = setlist.get("eventDate")
    if data:
        data = data.replace("-", "/")

    return Show(
        artist=artista,
        songs=_parse_songs(setlist, artista),
        venue=venue.get("name"),
        city=cidade,
        date=data,
        url=setlist.get("url"),
    )


def get_setlist(artist: str, city: Optional[str] = None, year: Optional[str] = None) -> Optional[Show]:
    """Busca na setlist.fm o show mais recente que tenha músicas registradas.

    Devolve None quando a busca rodou e não há resultado aproveitável. Levanta
    RuntimeError quando a API falhou — são coisas diferentes: mandar o usuário
    tentar outro nome quando a setlist.fm está fora só rende tentativa inútil.
    """
    headers = {"x-api-key": SETLIST_KEY, "Accept": "application/json"}
    params = {"artistName": artist, "p": 1}
    if city:
        params["cityName"] = city
    if year:
        params["year"] = year

    r = requests.get(API_URL, headers=headers, params=params, timeout=20)
    if r.status_code == 404:
        # A setlist.fm responde 404 quando a busca não casa com nada.
        logger.info("Nenhum show encontrado para %s (city=%s, year=%s)", artist, city, year)
        return None
    if r.status_code != 200:
        logger.error("Setlist.fm erro %s: %s", r.status_code, r.text[:200])
        raise RuntimeError(f"setlist.fm respondeu {r.status_code}")

    resultados = r.json().get("setlist", [])
    if not resultados:
        return None

    # Percorre os resultados em vez de olhar só o primeiro: show cancelado ou sem
    # setlist registrada é comum e vem no topo, o que fazia o bot responder
    # "não achei nenhuma setlist" mesmo havendo shows bons logo abaixo.
    for posicao, setlist in enumerate(resultados):
        show = _parse_show(setlist)
        if show.songs:
            if posicao:
                logger.info("Pulei %d show(s) sem músicas registradas.", posicao)
            logger.info("Usando setlist de %s (%d músicas).", show.describe(), len(show.songs))
            return show

    logger.info("Achei %d show(s) de %s, nenhum com músicas registradas.", len(resultados), artist)
    return None
