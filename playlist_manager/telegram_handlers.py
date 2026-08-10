import asyncio
import logging
from collections import OrderedDict
from functools import wraps
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from playlist_manager import messages
from playlist_manager.config import ALLOWED_TELEGRAM_IDS
from playlist_manager.errors import mensagem_de
from playlist_manager.integrations.llm import parse_request
from playlist_manager.integrations.setlist_fm import (
    SHOWS_RECENTES,
    average_setlist,
    get_recent_shows,
    get_setlist,
)
from playlist_manager.integrations.spotify import create_playlist_with_songs
from playlist_manager.models import Show

logger = logging.getLogger("playlist-bot")

# Prefixos do callback_data dos botões (o Telegram limita a 64 bytes).
ESCOLHA_SHOW = "show"
ESCOLHA_MEDIA = "media"

# Menus antigos continuam clicáveis no histórico do chat, então cada um recebe um
# token próprio: sem isso o botão de um pedido antigo montaria a playlist com a
# lista do pedido mais recente.
MENUS_GUARDADOS = 5


def somente_autorizados(handler):
    """Barra quem não está em ALLOWED_TELEGRAM_IDS, quando a lista está preenchida.

    A playlist é criada sempre na conta do Spotify de quem gerou o
    SPOTIFY_REFRESH_TOKEN. Sem esta trava, qualquer pessoa que descobrisse o bot
    escreveria na biblioteca do dono. Lista vazia mantém o bot aberto.
    """
    @wraps(handler)
    async def _guarda(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Lista vazia nem consulta o update: o bot aberto não paga nada por isso.
        if ALLOWED_TELEGRAM_IDS:
            user = update.effective_user
            if user is None or user.id not in ALLOWED_TELEGRAM_IDS:
                logger.warning("Pedido recusado: usuário %s fora da allowlist.",
                               getattr(user, "id", None))
                chat = update.effective_chat
                if chat is not None:
                    await context.bot.send_message(
                        chat_id=chat.id,
                        text=messages.NAO_AUTORIZADO,
                    )
                return
        return await handler(update, context)

    return _guarda


@somente_autorizados
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(messages.BOAS_VINDAS)


def _guardar_menu(context: ContextTypes.DEFAULT_TYPE, artist: str, shows: list[Show]) -> str:
    """Guarda a lista sob um token e devolve o token para os botões carregarem."""
    menus = context.user_data.setdefault("menus", OrderedDict())
    token = uuid4().hex[:8]
    menus[token] = {"artist": artist, "shows": shows}
    while len(menus) > MENUS_GUARDADOS:
        menus.popitem(last=False)
    return token


def _montar_menu(shows: list[Show], token: str) -> InlineKeyboardMarkup:
    botoes = [[InlineKeyboardButton(
        f"🎯 Setlist média ({len(shows)} shows)", callback_data=f"{ESCOLHA_MEDIA}:{token}"
    )]]
    for i, show in enumerate(shows):
        rotulo = " · ".join(p for p in (show.date, show.city or show.venue) if p) or f"Show {i + 1}"
        botoes.append([InlineKeyboardButton(
            f"{rotulo} ({len(show.songs)})", callback_data=f"{ESCOLHA_SHOW}:{token}:{i}"
        )])
    return InlineKeyboardMarkup(botoes)


async def _criar_e_responder(responder, show: Show, nome: str):
    """Monta a playlist e responde com o link e o que ficou de fora."""
    await responder(messages.criando_playlist(nome))

    url, adicionadas, faltando = await asyncio.to_thread(create_playlist_with_songs, show, nome)
    if not url:
        await responder(messages.NADA_NO_SPOTIFY)
        return
    await responder(messages.playlist_pronta(url, adicionadas, faltando))


@somente_autorizados
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    responder = update.message.reply_text
    await responder(messages.PROCURANDO)
    try:
        # openai, requests e spotipy são síncronos e um pedido leva dezenas de
        # segundos (uma busca no Spotify por música). Chamá-los direto travaria o
        # event loop, segurando o /health e os webhooks seguintes — justamente o
        # timeout do Telegram que a fila de updates existe para evitar.
        artist, city, year = await asyncio.to_thread(parse_request, text)
        if not artist:
            await responder(messages.ARTISTA_NAO_ENTENDIDO)
            return

        # Pedido específico ("São Paulo 2025") continua indo direto ao ponto;
        # só o pedido aberto ganha o menu de escolha.
        if city or year:
            show = await asyncio.to_thread(get_setlist, artist, city, year)
            if show is None:
                await responder(messages.SEM_SETLIST)
                return
            await responder(messages.achei_o_show(show))
            nome = f"Setlist {artist} {city or ''} {year or ''}".strip()
            await _criar_e_responder(responder, show, nome)
            return

        shows = await asyncio.to_thread(get_recent_shows, artist, None, None, SHOWS_RECENTES)
        if not shows:
            await responder(messages.SEM_SETLIST)
            return

        token = _guardar_menu(context, artist, shows)
        await responder(
            messages.escolha_um_show(artist, len(shows)),
            reply_markup=_montar_menu(shows, token),
        )
    except Exception as e:
        logger.exception("Erro ao processar pedido: %r", text)
        await responder(mensagem_de(e))


def _interpretar(data: str) -> tuple[str, str, int | None]:
    """Quebra o callback_data em (ação, token, índice). Levanta ValueError se torto."""
    partes = data.split(":")
    if len(partes) == 2 and partes[0] == ESCOLHA_MEDIA:
        return ESCOLHA_MEDIA, partes[1], None
    if len(partes) == 3 and partes[0] == ESCOLHA_SHOW:
        return ESCOLHA_SHOW, partes[1], int(partes[2])
    raise ValueError(data)


@somente_autorizados
async def handle_escolha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # tira o "carregando" do botão

    # query.message pode ser None ou inacessível quando o botão tem mais de 48h,
    # então a resposta sai pelo chat, não pela mensagem.
    chat = update.effective_chat

    async def responder(texto):
        await context.bot.send_message(chat_id=chat.id, text=texto)

    try:
        acao, token, indice = _interpretar(query.data)
    except ValueError:
        logger.warning("Callback data não reconhecido: %r", query.data)
        await responder(messages.ESCOLHA_NAO_RECONHECIDA)
        return

    menu = context.user_data.get("menus", {}).get(token)
    if menu is None:
        # user_data vive em memória: um restart do serviço leva as listas embora.
        await responder(messages.LISTA_EXPIROU)
        return

    # Montar a playlist leva dezenas de segundos; sem isso um toque duplo criaria
    # duas playlists idênticas.
    em_andamento = context.user_data.setdefault("em_andamento", set())
    if token in em_andamento:
        logger.info("Ignorando toque repetido no menu %s.", token)
        return
    em_andamento.add(token)

    try:
        await _remover_teclado(query)
        artist, shows = menu["artist"], menu["shows"]

        if acao == ESCOLHA_MEDIA:
            songs = average_setlist(shows)
            if not songs:
                await responder(messages.SEM_REPERTORIO_COMUM)
                return
            show = Show(artist=artist, songs=songs)
            nome = f"Setlist média {artist}"
            await responder(messages.media_montada(artist, len(songs), len(shows)))
        else:
            if not 0 <= indice < len(shows):
                logger.warning("Índice fora da lista: %r", query.data)
                await responder(messages.ESCOLHA_NAO_RECONHECIDA)
                return
            show = shows[indice]
            nome = f"Setlist {artist} {show.city or ''} {show.date or ''}".strip()
            await responder(messages.escolheu_o_show(show))

        # Fora do tratamento de escolha inválida: uma falha do Spotify aqui precisa
        # chegar como falha do Spotify, não como "não reconheci essa escolha".
        await _criar_e_responder(responder, show, nome)
    except Exception as e:
        logger.exception("Erro ao montar a escolha: %r", query.data)
        await responder(mensagem_de(e))
    finally:
        em_andamento.discard(token)


async def _remover_teclado(query) -> None:
    """Tira os botões da mensagem; falha aqui não pode atrapalhar o pedido."""
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        logger.debug("Não consegui remover o teclado: %s", e)
