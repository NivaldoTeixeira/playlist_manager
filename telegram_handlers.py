import asyncio
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
        # openai, requests e spotipy são síncronos e um pedido leva dezenas de
        # segundos (uma busca no Spotify por música). Chamá-los direto travaria o
        # event loop, segurando o /health e os webhooks seguintes — justamente o
        # timeout do Telegram que a fila de updates existe para evitar.
        artist, city, year = await asyncio.to_thread(parse_request, text)
        if not artist:
            await update.message.reply_text("Não entendi o artista... Confere o nome e tenta de novo, pfvr?")
            return

        show = await asyncio.to_thread(get_setlist, artist, city, year)
        if show is None:
            await update.message.reply_text("Não achei nenhuma setlist 😬")
            return

        await update.message.reply_text(
            f"Achei: {show.describe()} ({len(show.songs)} músicas).\n"
            "Booa, criando sua playlist no Spotify..."
        )
        nome = f"Setlist {artist} {city or ''} {year or ''}".strip()
        url, adicionadas, faltando = await asyncio.to_thread(create_playlist_with_songs, show, nome)

        if not url:
            await update.message.reply_text(
                "Achei a setlist, mas não encontrei nenhuma dessas músicas no Spotify... Sorry 😬"
            )
            return

        resposta = f"Tá na mão ({adicionadas} músicas): {url}"
        if faltando:
            # Antes as músicas sem match sumiam caladas e a playlist vinha menor
            # que a setlist sem explicação.
            amostra = ", ".join(faltando[:5])
            resto = f" e mais {len(faltando) - 5}" if len(faltando) > 5 else ""
            resposta += f"\n\nNão achei no Spotify: {amostra}{resto}."
        await update.message.reply_text(resposta)
    except Exception:
        # Detalhe da exceção fica no log; o usuário não precisa (nem deve) ver o texto cru.
        logger.exception("Erro ao processar pedido: %r", text)
        await update.message.reply_text("Deu erro aqui do meu lado... tenta de novo daqui a pouco? 😬")
