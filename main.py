import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, PlainTextResponse
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import TELEGRAM_TOKEN, WEBHOOK_SECRET, missing_config
from telegram_handlers import cmd_start, handle_escolha, handle_text

from spotify_utils import make_auth_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("playlist-bot")

# --- Telegram app (webhook mode) ---
tg_app = Application.builder().token(TELEGRAM_TOKEN).build()

tg_app.add_handler(CommandHandler("start", cmd_start))
tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
tg_app.add_handler(CallbackQueryHandler(handle_escolha))


@asynccontextmanager
async def lifespan(app: FastAPI):
    faltando = missing_config()
    if faltando:
        # Aviso, não erro: o serviço sobe mesmo assim e /health mostra o que falta.
        logger.warning("Variáveis de ambiente ausentes: %s", ", ".join(faltando))

    await tg_app.initialize()
    # start() liga o consumidor da update_queue, que processa os updates em
    # segundo plano — o webhook só enfileira e responde na hora.
    await tg_app.start()
    logger.info("Bot inicializado.")
    try:
        yield
    finally:
        await tg_app.stop()
        await tg_app.shutdown()
        logger.info("Bot encerrado.")


# --- FastAPI app ---
app = FastAPI(title="Playlist Manager Bot", lifespan=lifespan)


# ---------- FASTAPI ROUTES ----------
@app.get("/health")
def health():
    faltando = missing_config()
    return {"ok": not faltando, "missing_config": faltando}

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

    # O valor é mostrado na resposta e não vai para o log: o log do Render fica
    # retido e este token não expira sozinho, então gravá-lo deixaria uma
    # credencial viva em texto puro no painel.
    logger.info("Refresh token do Spotify gerado com sucesso.")
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
    # DEBUG e não INFO: o payload traz o texto da mensagem e o id do chat, que em
    # produção não têm por que ficar retidos no log.
    logger.debug("Recebido update do Telegram: %s", data)

    update = Update.de_json(data, tg_app.bot)
    if update is None:
        logger.warning("Update do Telegram não reconhecido, ignorando.")
        return PlainTextResponse("ok")

    # Enfileira e responde imediatamente: criar a playlist leva mais tempo que o
    # timeout do Telegram, e demorar aqui faria ele reenviar o mesmo update.
    await tg_app.update_queue.put(update)
    return PlainTextResponse("ok")
