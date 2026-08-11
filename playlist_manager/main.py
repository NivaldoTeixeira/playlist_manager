"""App FastAPI: as rotas HTTP e o ciclo de vida do bot do Telegram.

O bot roda em modo webhook. O Telegram entrega os updates em `POST /webhook`,
que só os enfileira; quem processa é o consumidor da `update_queue`, ligado no
lifespan. As outras rotas existem para operar o serviço: `/health` diz o que
falta configurar e `/login` + `/callback` geram o refresh token do Spotify.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from playlist_manager.config import TELEGRAM_TOKEN, WEBHOOK_SECRET, missing_config
from playlist_manager.integrations.spotify import make_auth_manager
from playlist_manager.telegram_handlers import cmd_start, handle_escolha, handle_text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("playlist-bot")


# Quantos pedidos podem estar em andamento ao mesmo tempo. O padrão do
# python-telegram-bot é 1, o que processa os updates em fila: um pedido leva
# dezenas de segundos, então o segundo usuário esperaria o primeiro terminar —
# e o `to_thread` do service, que existe justamente para isso não acontecer,
# ficava sem efeito. Também é o que faz a trava de toque duplo em
# telegram_handlers ter função: sem concorrência, o segundo toque só era
# processado depois do primeiro terminar e liberar o token.
UPDATES_SIMULTANEOS = 8


def build_telegram_app() -> Application:
    """Monta o bot com os handlers registrados, sem tocar na rede.

    É uma função e não um global montado no import porque efeito colateral de
    import é ruim de testar e de diagnosticar: qualquer problema aqui derrubava
    o processo antes de qualquer rota existir. A chamada de rede continua no
    `initialize()`, dentro do lifespan.
    """
    tg_app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .concurrent_updates(UPDATES_SIMULTANEOS)
        .build()
    )
    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    tg_app.add_handler(CallbackQueryHandler(handle_escolha))
    return tg_app


@asynccontextmanager
async def lifespan(app: FastAPI):
    faltando = missing_config()
    if faltando:
        # Aviso, não erro: o serviço sobe mesmo assim e /health mostra o que falta.
        logger.warning("Variáveis de ambiente ausentes: %s", ", ".join(faltando))

    if not TELEGRAM_TOKEN:
        # Sem token o python-telegram-bot recusa construir o bot. Subir assim
        # mesmo, sem bot, é melhor que morrer no boot: o /health responde e diz
        # o que falta, que é exatamente para isso que ele existe. O webhook
        # devolve 503 enquanto durar.
        logger.error("TELEGRAM_TOKEN ausente: subindo sem o bot. Veja /health.")
        yield
        return

    tg_app = build_telegram_app()
    # Em app.state e não num global: é o que as rotas leem, e deixa o teste
    # montar o bot sem subir o lifespan (que faria uma chamada getMe de verdade).
    app.state.tg_app = tg_app

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


app = FastAPI(
    title="Playlist Manager Bot",
    description=(
        "Monta playlists no Spotify a partir de setlists reais de shows, "
        "a partir de um pedido em linguagem natural no Telegram."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ---------- FASTAPI ROUTES ----------
@app.get("/health")
def health():
    """Status do serviço e o que falta configurar.

    Responde 200 mesmo com variáveis faltando: o objetivo é justamente dizer
    quais são, e não sumir do ar junto com o problema.
    """
    faltando = missing_config()
    return {"ok": not faltando, "missing_config": faltando}


@app.get("/login")
def login():
    """Redireciona para a autorização do Spotify, o primeiro passo do refresh token."""
    auth = make_auth_manager()
    return RedirectResponse(auth.get_authorize_url())


@app.get("/callback")
def callback(code: str | None = None, error: str | None = None):
    """Recebe o retorno do Spotify e mostra o SPOTIFY_REFRESH_TOKEN gerado."""
    if error:
        return PlainTextResponse(f"Erro do Spotify: {error}", status_code=400)
    if not code:
        return PlainTextResponse("Faltou o parâmetro ?code=...", status_code=400)

    am = make_auth_manager()
    # check_cache=False: com o cache ligado o spotipy devolveria um token já
    # guardado em vez de trocar este código. Esta rota é usada justamente quando
    # o token velho não serve mais, então reaproveitá-lo devolveria o valor
    # errado — ou estouraria, se ele tivesse sido revogado.
    token_info = am.get_access_token(code, as_dict=True, check_cache=False)
    refresh = token_info.get("refresh_token")

    if not refresh:
        return PlainTextResponse(
            "Não veio refresh_token. Tente novamente com show_dialog=true.", status_code=400
        )

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
    """Recebe um update do Telegram, enfileira e responde na hora.

    O `token` da URL é o TELEGRAM_WEBHOOK_SECRET e funciona como senha: sem ele
    a chamada leva 403.
    """
    if token != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")

    tg_app: Application | None = getattr(request.app.state, "tg_app", None)
    if tg_app is None:
        # Ou o update chegou antes de o lifespan terminar, ou não há TELEGRAM_TOKEN
        # e o bot nem foi montado. 503 diz ao Telegram para tentar de novo, em vez
        # de descartar o update com um 500.
        logger.error("Update recebido sem o bot pronto (falta TELEGRAM_TOKEN?).")
        raise HTTPException(status_code=503, detail="bot ainda não inicializado")

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
