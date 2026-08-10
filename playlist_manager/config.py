"""Leitura das variáveis de ambiente, num lugar só.

Os valores são lidos no import e ficam como constantes de módulo — é o que
permite `from playlist_manager.config import SETLIST_KEY` nas integrações. A
exceção é `missing_config()`, que consulta o ambiente na hora da chamada: ela
serve ao `/health`, que precisa dizer o estado atual e não o do boot.
"""

import logging
import os

from dotenv import load_dotenv

logger = logging.getLogger("playlist-bot")

# Carrega o .env em desenvolvimento local. Em produção (Render) não existe .env
# e as variáveis já vêm do ambiente, então isso vira um no-op.
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")
SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")
SETLIST_KEY = os.getenv("SETLIST_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

SCOPES = "playlist-modify-public playlist-modify-private"


def _ids_permitidos(bruto: str | None) -> frozenset[int]:
    """Lê ALLOWED_TELEGRAM_IDS, ignorando entradas que não são número.

    Entrada torta vira aviso no log em vez de derrubar o boot: um caractere a
    mais na variável não pode tirar o serviço do ar.
    """
    ids = set()
    for parte in (bruto or "").replace(";", ",").split(","):
        parte = parte.strip()
        if not parte:
            continue
        try:
            ids.add(int(parte))
        except ValueError:
            logger.warning("ALLOWED_TELEGRAM_IDS: ignorando %r, que não é um id numérico.", parte)
    return frozenset(ids)


# Quem pode usar o bot. Vazio = liberado para qualquer um, que é o padrão
# histórico — mas veja o aviso no README: a playlist nasce sempre na conta do
# Spotify de quem gerou o SPOTIFY_REFRESH_TOKEN, não na de quem pediu.
ALLOWED_TELEGRAM_IDS = _ids_permitidos(os.getenv("ALLOWED_TELEGRAM_IDS"))

# Variáveis sem as quais o bot não consegue atender um pedido de ponta a ponta.
# Só os nomes: antes cada uma aparecia três vezes no arquivo (constante, chave e
# valor), e a lista congelava no import — se você corrigisse a variável no Render
# sem reiniciar, o /health continuava reclamando dela.
_OBRIGATORIAS = (
    "TELEGRAM_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "SPOTIPY_CLIENT_ID",
    "SPOTIPY_CLIENT_SECRET",
    "SPOTIPY_REDIRECT_URI",
    "SPOTIFY_REFRESH_TOKEN",
    "SETLIST_KEY",
    "OPENAI_API_KEY",
)


def missing_config() -> list[str]:
    """Nomes das variáveis obrigatórias que estão vazias ou ausentes, agora."""
    return sorted(nome for nome in _OBRIGATORIAS if not os.getenv(nome))
