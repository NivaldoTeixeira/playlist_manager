import requests
import logging
from typing import Optional
from config import SETLIST_KEY

logger = logging.getLogger("playlist-bot")

def get_setlist(artist: str, city: Optional[str] = None, year: Optional[str] = None) -> list[str]:
    url = "https://api.setlist.fm/rest/1.0/search/setlists"
    headers = {"x-api-key": SETLIST_KEY, "Accept": "application/json"}

    params = {"artistName": artist, "p": 1}
    if city:
        params["cityName"] = city
    if year:
        params["year"] = year

    r = requests.get(url, headers=headers, params=params, timeout=20)
    if r.status_code != 200:
        logger.error("Setlist.fm erro %s: %s", r.status_code, r.text[:200])
        return []

    data = r.json()
    items = data.get("setlist", [])
    if not items:
        return []

    sets = items[0].get("sets", {}).get("set", [])
    songs = []
    for s in sets:
        for song in s.get("song", []):
            name = song.get("name")
            if name:
                songs.append(name)
    return songs