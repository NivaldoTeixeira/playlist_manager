import logging
from typing import Optional

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, PlainTextResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from config import TELEGRAM_TOKEN, WEBHOOK_SECRET
from telegram_handlers import cmd_start, handle_text

from spotify_utils import make_auth_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("playlist-bot")

# --- FastAPI app ---
app = FastAPI(title="Playlist Manager Bot")

# --- Telegram app (webhook mode) ---
tg_app = Application.builder().token(TELEGRAM_TOKEN).build()

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
