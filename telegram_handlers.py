import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from openai_utils import parse_request, InterpretacaoIndisponivel
from setlist_utils import (
    Show,
    average_setlist,
    get_recent_shows,
    get_setlist,
    SetlistIndisponivel,
    SHOWS_RECENTES,
)
from spotify_utils import create_playlist_with_songs, SpotifyIndisponivel

logger = logging.getLogger("playlist-bot")

# Prefixos do callback_data dos botões (o Telegram limita a 64 bytes).
ESCOLHA_SHOW = "show"
ESCOLHA_MEDIA = "media"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 Oi! Qual playlist quer criar? Me fale o nome da banda, a cidade e ano do show que monto pra vc. \n"
        "Ex: 'Playlist do Good Charlotte, São Paulo 2025'\n\n"
        "Se não disser cidade nem ano, eu te mostro os últimos shows para escolher — "
        "ou monto a setlist média do artista."
    )


def _mensagem_de_erro(exc: BaseException) -> str:
    """Traduz a falha para algo acionável, em vez de um 'deu erro' genérico."""
    if isinstance(exc, InterpretacaoIndisponivel):
        return ("Não consegui interpretar seu pedido agora — o serviço de IA não respondeu. "
                "Tenta de novo daqui a pouco? 😬")
    if isinstance(exc, SetlistIndisponivel):
        return ("A setlist.fm não está respondendo agora, então não consigo buscar o show. "
                "Tenta de novo daqui a pouco? 😬")
    if isinstance(exc, SpotifyIndisponivel):
        return ("Não consegui falar com o Spotify — a autorização pode ter vencido. "
                "Se persistir, refaça o /login e atualize o SPOTIFY_REFRESH_TOKEN. 😬")
    return "Deu erro aqui do meu lado... tenta de novo daqui a pouco? 😬"


def _montar_menu(shows: list[Show]) -> InlineKeyboardMarkup:
    botoes = [[InlineKeyboardButton(
        f"🎯 Setlist média ({len(shows)} shows)", callback_data=ESCOLHA_MEDIA
    )]]
    for i, show in enumerate(shows):
        rotulo = " · ".join(p for p in (show.date, show.city or show.venue) if p) or f"Show {i + 1}"
        botoes.append([InlineKeyboardButton(
            f"{rotulo} ({len(show.songs)})", callback_data=f"{ESCOLHA_SHOW}:{i}"
        )])
    return InlineKeyboardMarkup(botoes)


async def _criar_e_responder(responder, show: Show, nome: str):
    """Monta a playlist e responde com o link e o que ficou de fora."""
    await responder(f"Booa, criando “{nome}” no Spotify...")

    url, adicionadas, faltando = await asyncio.to_thread(create_playlist_with_songs, show, nome)
    if not url:
        await responder("Não encontrei nenhuma dessas músicas no Spotify... Sorry 😬")
        return

    resposta = f"Tá na mão ({adicionadas} músicas): {url}"
    if faltando:
        # Antes as músicas sem match sumiam caladas e a playlist vinha menor
        # que a setlist sem explicação.
        amostra = ", ".join(faltando[:5])
        resto = f" e mais {len(faltando) - 5}" if len(faltando) > 5 else ""
        resposta += f"\n\nNão achei no Spotify: {amostra}{resto}."
    await responder(resposta)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    responder = update.message.reply_text
    await responder("Deixa eu ver o que eu acho... 🔎")
    try:
        # openai, requests e spotipy são síncronos e um pedido leva dezenas de
        # segundos (uma busca no Spotify por música). Chamá-los direto travaria o
        # event loop, segurando o /health e os webhooks seguintes — justamente o
        # timeout do Telegram que a fila de updates existe para evitar.
        artist, city, year = await asyncio.to_thread(parse_request, text)
        if not artist:
            await responder("Não entendi o artista... Confere o nome e tenta de novo, pfvr?")
            return

        # Pedido específico ("São Paulo 2025") continua indo direto ao ponto;
        # só o pedido aberto ganha o menu de escolha.
        if city or year:
            show = await asyncio.to_thread(get_setlist, artist, city, year)
            if show is None:
                await responder("Não achei nenhuma setlist 😬")
                return
            await responder(f"Achei: {show.describe()} ({len(show.songs)} músicas).")
            nome = f"Setlist {artist} {city or ''} {year or ''}".strip()
            await _criar_e_responder(responder, show, nome)
            return

        shows = await asyncio.to_thread(get_recent_shows, artist, None, None, SHOWS_RECENTES)
        if not shows:
            await responder("Não achei nenhuma setlist 😬")
            return

        # Guardado para o clique no botão; o callback só carrega o índice.
        context.user_data["escolha"] = {"artist": artist, "shows": shows}
        await responder(
            f"Achei os {len(shows)} shows mais recentes do {artist}. "
            "Escolhe um, ou pega a setlist média:",
            reply_markup=_montar_menu(shows),
        )
    except Exception as e:
        logger.exception("Erro ao processar pedido: %r", text)
        await responder(_mensagem_de_erro(e))


async def handle_escolha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # tira o "carregando" do botão
    responder = query.message.reply_text

    pendente = context.user_data.get("escolha")
    if not pendente:
        # user_data vive em memória: um restart do serviço leva a lista embora.
        await responder("Essa lista expirou 😅 Manda o pedido de novo que eu busco os shows.")
        return

    try:
        artist, shows = pendente["artist"], pendente["shows"]

        if query.data == ESCOLHA_MEDIA:
            songs = average_setlist(shows)
            if not songs:
                await responder(
                    "Esses shows não têm repertório em comum suficiente para uma média. "
                    "Escolhe um show específico?"
                )
                return
            show = Show(artist=artist, songs=songs)
            nome = f"Setlist média {artist}"
            await responder(
                f"Setlist média do {artist}: {len(songs)} músicas que aparecem "
                f"na maioria dos últimos {len(shows)} shows."
            )
        else:
            indice = int(query.data.split(":", 1)[1])
            show = shows[indice]
            nome = f"Setlist {artist} {show.city or ''} {show.date or ''}".strip()
            await responder(f"Beleza: {show.describe()} ({len(show.songs)} músicas).")

        await _criar_e_responder(responder, show, nome)
    except (IndexError, ValueError, KeyError):
        logger.exception("Escolha inválida: %r", query.data)
        await responder("Não reconheci essa escolha 😅 Manda o pedido de novo?")
    except Exception as e:
        logger.exception("Erro ao montar a escolha: %r", query.data)
        await responder(_mensagem_de_erro(e))
