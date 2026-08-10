"""Consulta à API da setlist.fm e o cálculo da setlist média."""

import logging
from collections import Counter
from dataclasses import replace
from math import ceil

import requests

from playlist_manager.config import SETLIST_KEY
from playlist_manager.errors import SetlistIndisponivel
from playlist_manager.models import Show, Song

logger = logging.getLogger("playlist-bot")

API_URL = "https://api.setlist.fm/rest/1.0/search/setlists"

# Quantos shows recentes oferecer quando o pedido não diz cidade nem ano.
SHOWS_RECENTES = 10

# Fração dos shows em que a música precisa aparecer para entrar na setlist média.
# Abaixo disso a média vira uma lista inchada de raridades de uma noite só.
FREQUENCIA_MINIMA = 0.5


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


def get_recent_shows(
    artist: str,
    city: str | None = None,
    year: str | None = None,
    limit: int = SHOWS_RECENTES,
) -> list[Show]:
    """Shows recentes do artista que tenham músicas registradas, mais novo primeiro.

    Devolve lista vazia quando a busca rodou e não há resultado aproveitável.
    Levanta SetlistIndisponivel quando a API falhou — são coisas diferentes:
    mandar o usuário tentar outro nome com a setlist.fm fora só rende tentativa
    inútil.
    """
    headers = {"x-api-key": SETLIST_KEY, "Accept": "application/json"}
    params = {"artistName": artist, "p": 1}
    if city:
        params["cityName"] = city
    if year:
        params["year"] = year

    try:
        r = requests.get(API_URL, headers=headers, params=params, timeout=20)
    except requests.RequestException as e:
        logger.warning("Não consegui falar com a setlist.fm: %s", e)
        raise SetlistIndisponivel(str(e)) from e

    if r.status_code == 404:
        # A setlist.fm responde 404 quando a busca não casa com nada.
        logger.info("Nenhum show encontrado para %s (city=%s, year=%s)", artist, city, year)
        return []
    if r.status_code != 200:
        logger.error("Setlist.fm erro %s: %s", r.status_code, r.text[:200])
        raise SetlistIndisponivel(f"setlist.fm respondeu {r.status_code}")

    resultados = r.json().get("setlist", [])
    if not resultados:
        return []

    # Filtra os que não têm músicas em vez de olhar só o primeiro: show cancelado
    # ou sem setlist registrada é comum e vem no topo, o que fazia o bot responder
    # "não achei nenhuma setlist" mesmo havendo shows bons logo abaixo.
    shows = [s for s in map(_parse_show, resultados) if s.songs]
    ignorados = len(resultados) - len(shows)
    if ignorados:
        logger.info("Ignorei %d show(s) sem músicas registradas.", ignorados)

    return shows[:limit]


def get_setlist(artist: str, city: str | None = None, year: str | None = None) -> Show | None:
    """O show recente mais relevante, ou None se não houver nenhum aproveitável."""
    shows = get_recent_shows(artist, city, year, limit=1)
    if not shows:
        return None
    logger.info("Usando setlist de %s (%d músicas).", shows[0].describe(), len(shows[0].songs))
    return shows[0]


def average_setlist(shows: list[Show], min_frequency: float = FREQUENCIA_MINIMA) -> list[Song]:
    """Repertório típico do artista a partir dos shows dados.

    A setlist.fm calcula isso no site, mas não expõe na API 1.0 — então a conta é
    feita aqui: entram as músicas presentes em pelo menos `min_frequency` dos
    shows, da mais recorrente para a menos.

    A ordem aqui é informativa; quem define a ordem da playlist é `_ordenar()`,
    em spotify_utils, que não segue o roteiro do show de propósito.
    """
    if not shows:
        return []

    # Conta em quantos SHOWS a música apareceu, não quantas vezes foi tocada:
    # música repetida na mesma noite (bis, medley) não pode contar dobrado e
    # inflar a presença dela na média.
    em_shows: Counter = Counter()
    exemplar: dict[str, Song] = {}

    for show in shows:
        for chave in {s.name.casefold() for s in show.songs}:
            em_shows[chave] += 1
        for song in show.songs:
            chave = song.name.casefold()
            anterior = exemplar.get(chave)
            # Prefere o registro que identifica o cover: se um show anotou o
            # artista original e outro não, ficar com o segundo faria a busca no
            # Spotify procurar a música pela banda do show e não achar nada.
            if anterior is None or (
                anterior.search_artist == show.artist and song.search_artist != show.artist
            ):
                exemplar[chave] = song

    minimo = max(1, ceil(len(shows) * min_frequency))
    frequentes = [chave for chave, n in em_shows.items() if n >= minimo]
    frequentes.sort(key=lambda chave: (-em_shows[chave], exemplar[chave].name.casefold()))

    logger.info(
        "Setlist média de %d shows: %d músicas (presentes em %d shows ou mais).",
        len(shows), len(frequentes), minimo,
    )
    return [replace(exemplar[chave], plays=em_shows[chave]) for chave in frequentes]
