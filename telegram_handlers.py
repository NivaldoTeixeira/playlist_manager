import logging
from telegram import Update
from telegram.ext import ContextTypes
from openai_utils import parse_request
from setlist_utils import get_setlist
from spotify_utils import create_playlist_with_songs

logger = logging.getLogger("playlist-bot")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 Oi! Me peça algo como:\n"
        "• 'Cria playlist do show do Coldplay em São Paulo 2022'\n"
        "• 'Quero a setlist mais recente do Metallica'"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    await update.message.reply_text("🔎 Interpretando seu pedido...")
    try:
        artist, city, year = parse_request(text)
        if not artist:
            await update.message.reply_text("Não entendi o artista. Tente: 'playlist do Coldplay em SP 2022'.")
            return

        songs = get_setlist(artist, city, year)
        if not songs:
            await update.message.reply_text("⚠️ Não encontrei setlist.")
            return

        await update.message.reply_text("🎧 Criando sua playlist no Spotify...")
        url = create_playlist_with_songs(artist, songs, playlist_name=f"Setlist {artist} {city or ''} {year or ''}".strip())
        if url:
            await update.message.reply_text(f"✅ Sua playlist: {url}")
        else:
            await update.message.reply_text("Algo falhou ao criar a playlist.")
    except Exception as e:
        logger.exception("Erro handle_text: %s", e)
        await update.message.reply_text(f"😕 Ocorreu um erro: {e}")
