import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from math import ceil
from statistics import mean
from typing import Optional

import requests

from config import SETLIST_KEY

logger = logging.getLogger("playlist-bot")

API_URL = "https://api.setlist.fm/rest/1.0/search/setlists"

# Quantos shows recentes oferecer quando o pedido não diz cidade nem ano.
SHOWS_RECENTES = 10

# Fração dos shows em que a música precisa aparecer para entrar na setlist média.
# Abaixo disso a média vira uma lista inchada de raridades de uma noite só.
FREQUENCIA_MINIMA = 0.5


class SetlistIndisponivel(RuntimeError):
    """A setlist.fm não respondeu. Diferente de não existir show cadastrado."""


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


def get_recent_shows(
    artist: str,
    city: Optional[str] = None,
    year: Optional[str] = None,
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


def get_setlist(artist: str, city: Optional[str] = None, year: Optional[str] = None) -> Optional[Show]:
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
    shows, ordenadas pela posição média que ocupam.
    """
    if not shows:
        return []

    contagem: Counter = Counter()
    posicoes: dict[str, list[float]] = defaultdict(list)
    exemplar: dict[str, Song] = {}

    for show in shows:
        ultimo = max(len(show.songs) - 1, 1)
        for i, song in enumerate(show.songs):
            chave = song.name.casefold()
            contagem[chave] += 1
            # Posição relativa (0 = abertura, 1 = encerramento) para comparar
            # shows de tamanhos diferentes.
            posicoes[chave].append(i / ultimo)
            exemplar.setdefault(chave, song)

    minimo = max(1, ceil(len(shows) * min_frequency))
    frequentes = [chave for chave, n in contagem.items() if n >= minimo]
    frequentes.sort(key=lambda chave: mean(posicoes[chave]))

    logger.info(
        "Setlist média de %d shows: %d músicas (mínimo de %d aparições).",
        len(shows), len(frequentes), minimo,
    )
    return [replace(exemplar[chave], plays=contagem[chave]) for chave in frequentes]
