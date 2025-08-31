import logging
from typing import Optional

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, PlainTextResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from config import TELEGRAM_TOKEN, WEBHOOK_SECRET, SETLIST_KEY, OPENAI_API_KEY
from spotify_utils import make_auth_manager, create_playlist_with_songs
from openai_utils import parse_request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("playlist-bot")

# --- FastAPI app ---
app = FastAPI(title="Playlist Manager Bot")

# --- Telegram app (webhook mode) ---
tg_app = Application.builder().token(TELEGRAM_TOKEN).build()

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
