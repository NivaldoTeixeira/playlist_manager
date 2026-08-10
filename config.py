import os

from dotenv import load_dotenv

# Carrega o .env em desenvolvimento local. Em produção (Render) não existe .env
# e as variáveis já vêm do ambiente, então isso vira um no-op.
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")
SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")
SETLIST_KEY = os.getenv("SETLIST_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

SCOPES = "playlist-modify-public playlist-modify-private"

# Variáveis sem as quais o bot não consegue atender um pedido de ponta a ponta.
_REQUIRED = {
    "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
    "TELEGRAM_WEBHOOK_SECRET": WEBHOOK_SECRET,
    "SPOTIPY_CLIENT_ID": SPOTIPY_CLIENT_ID,
    "SPOTIPY_CLIENT_SECRET": SPOTIPY_CLIENT_SECRET,
    "SPOTIPY_REDIRECT_URI": SPOTIPY_REDIRECT_URI,
    "SPOTIFY_REFRESH_TOKEN": SPOTIFY_REFRESH_TOKEN,
    "SETLIST_KEY": SETLIST_KEY,
    "OPENAI_API_KEY": OPENAI_API_KEY,
}


def missing_config() -> list[str]:
    """Nomes das variáveis obrigatórias que estão vazias ou ausentes."""
    return sorted(name for name, value in _REQUIRED.items() if not value)
