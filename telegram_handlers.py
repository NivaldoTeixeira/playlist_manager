import logging
from telegram import Update
from telegram.ext import ContextTypes
from openai_utils import parse_request
from setlist_utils import get_setlist
from spotify_utils import create_playlist_with_songs

logger = logging.getLogger("playlist-bot")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 Oi! Qual playlist quer criar? Me fale o nome da banda, a cidade e ano do show que monto pra vc. \n"
        "Ex: 'Playlist do Good Charlotte, São Paulo 2025'"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    await update.message.reply_text("Deixa eu ver o que eu acho... 🔎")
    try:
        artist, city, year = parse_request(text)
        if not artist:
            await update.message.reply_text("Não entendi o artista... Confere o nome e tenta de novo, pfvr?")
            return

        songs = get_setlist(artist, city, year)
        if not songs:
            await update.message.reply_text("Não achei nenhuma setlist 😬")
            return

        await update.message.reply_text("Booa, criando sua playlist no Spotify...")
        url = create_playlist_with_songs(artist, songs, playlist_name=f"Setlist {artist} {city or ''} {year or ''}".strip())
        if url:
            await update.message.reply_text(f"Tá na mão: {url}")
        else:
            await update.message.reply_text("Deu algum problema criando a playlist... Sorry 😬")
    except Exception as e:
        logger.exception("Erro handle_text: %s", e)
        await update.message.reply_text(f"Deu erro... o que é isso? {e}")
