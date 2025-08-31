import os
import logging
from typing import Optional

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, PlainTextResponse
from openai import OpenAI
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("playlist-bot")

# --- ENV ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")
SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")  # https://playlist-manager-0ln7.onrender.com/callback
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")  # depois de gerar via /login
SETLIST_KEY = os.getenv("SETLIST_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

SCOPES = "playlist-modify-public playlist-modify-private"

# --- FastAPI app ---
app = FastAPI(title="Playlist Manager Bot")

# --- Telegram app (webhook mode) ---
tg_app = Application.builder().token(TELEGRAM_TOKEN).build()

# --- OpenAI client ---
oa_client = OpenAI(api_key=OPENAI_API_KEY)

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

# ---------- SETLIST.FM ----------
def get_setlist(artist: str, city: Optional[str] = None, year: Optional[str] = None):
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

# ---------- OPENAI: PARSING NATURAL ----------
def parse_request(text: str):
    """
    Usa LLM para extrair {artist, city, year} do pedido.
    Retorna uma tupla (artist, city, year), podendo ser None se não for identificado.
    """
    import json
    try:
        prompt = f"""
        Extraia do texto a seguir os campos JSON: artist, city, year (YYYY).
        Se não houver city ou year, retorne null. Não invente.
        Retorne apenas um JSON puro, sem nenhum outro texto ou markdown.
        Texto: "{text}"
        """
        resp = oa_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        content = resp.choices[0].message.content.strip()

        # Remove possíveis blocos de código Markdown
        if content.startswith("```") and content.endswith("```"):
            content = "\n".join(content.splitlines()[1:-1])

        data = json.loads(content)
        artist = data.get("artist") or None
        city = data.get("city") or None
        year = data.get("year") or None
        return artist, city, year

    except json.JSONDecodeError:
        logger.warning("Não consegui decodificar JSON do LLM: %s", content)
        return None, None, None
    except Exception as e:
        logger.warning("Erro no parse_request: %s", e)
        return None, None, None


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

# ---------- TELEGRAM HANDLERS ----------
async def cmd_start(update, context):
    await update.message.reply_text(
        "🎵 Oi! Me peça algo como:\n"
        "• 'Cria playlist do show do Coldplay em São Paulo 2022'\n"
        "• 'Quero a setlist mais recente do Metallica'\n"
        "Eu vou buscar a setlist no setlist.fm e criar a playlist no Spotify."
    )

async def handle_text(update, context):
    text = update.message.text.strip()
    await update.message.reply_text("🔎 Interpretando seu pedido...")
    try:
        artist, city, year = parse_request(text)
        if not artist:
            await update.message.reply_text("Não entendi o artista. Tente: 'playlist do Coldplay em SP 2022'.")
            return

        songs = get_setlist(artist, city, year)
        if not songs:
            msg = "Não encontrei setlist."
            if city or year:
                msg += f" (filtros: cidade={city or '-'}, ano={year or '-'})"
            await update.message.reply_text(f"⚠️ {msg}")
            return

        await update.message.reply_text("🎧 Criando sua playlist no Spotify...")
        url = create_playlist_with_songs(artist, songs, playlist_name=f"Setlist {artist} {city or ''} {year or ''}".strip())
        if url:
            await update.message.reply_text(f"✅ Pronto! Sua playlist:\n{url}")
        else:
            await update.message.reply_text("Algo falhou ao criar a playlist.")
    except Exception as e:
        logger.exception("Erro no handler: %s", e)
        await update.message.reply_text(f"😕 Ocorreu um erro: {e}")

tg_app.add_handler(CommandHandler("start", cmd_start))
tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

# ---------- FASTAPI ROUTES ----------
@app.get("/health")
def health():
    return {"ok": True}

@app.get("/login")
def login():
    auth = make_auth_manager()
    return RedirectResponse(auth.get_authorize_url())

@app.get("/callback")
def callback(code: Optional[str] = None, error: Optional[str] = None):
    if error:
        return PlainTextResponse(f"Erro do Spotify: {error}", status_code=400)
    if not code:
        return PlainTextResponse("Faltou o parâmetro ?code=...", status_code=400)

    am = make_auth_manager()
    token_info = am.get_access_token(code, as_dict=True)
    refresh = token_info.get("refresh_token")

    if not refresh:
        return PlainTextResponse("Não veio refresh_token. Tente novamente com show_dialog=true.", status_code=400)

    logger.info("Seu SPOTIFY_REFRESH_TOKEN: %s", refresh)
    return PlainTextResponse(
        "✅ Autorizado!\n\n"
        f"SPOTIFY_REFRESH_TOKEN = {refresh}\n\n"
        "Salve nas variáveis do Render e reinicie o serviço."
    )

@app.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request):
    if token != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")
    data = await request.json()
    logger.info("Recebido update do Telegram: %s", data)
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return PlainTextResponse("ok")

# Inicializa/encerra o app do Telegram junto com a FastAPI
@app.on_event("startup")
async def on_startup():
    await tg_app.initialize()

@app.on_event("shutdown")
async def on_shutdown():
    await tg_app.shutdown()
