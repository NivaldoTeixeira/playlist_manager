import logging
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from config import SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, SPOTIPY_REDIRECT_URI, SCOPES, SPOTIFY_REFRESH_TOKEN

logger = logging.getLogger("playlist-bot")

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
        raise RuntimeError("SPOTIFY_REFRESH_TOKEN não configurado. Use /login para gerar.")
    am = make_auth_manager()
    token_info = am.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
    access_token = token_info["access_token"]
    return spotipy.Spotify(auth=access_token)

# ---------- SPOTIFY: CRIAR PLAYLIST ----------
def create_playlist_with_songs(artist: str, songs: list[str], playlist_name: Optional[str] = None) -> Optional[str]:
    sp = get_spotify_client()
    me = sp.current_user()["id"]
    name = playlist_name or f"Setlist {artist}"
    playlist = sp.user_playlist_create(user=me, name=name, public=True, description=f"Gerada pelo bot - {artist}")
    pid = playlist["id"]

    track_ids = []
    for s in songs:
        q = f'track:"{s}" artist:"{artist}"'
        res = sp.search(q=q, limit=1, type="track")
        items = res.get("tracks", {}).get("items", [])
        if items:
            track_ids.append(items[0]["id"])

    for i in range(0, len(track_ids), 100):
        sp.playlist_add_items(pid, track_ids[i:i+100])

    return playlist["external_urls"]["spotify"]