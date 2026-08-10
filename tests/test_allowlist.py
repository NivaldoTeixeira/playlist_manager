"""Trava de acesso: a playlist nasce na conta do dono, não na de quem pede.

Sem a allowlist, qualquer pessoa que descobrisse o bot escreveria na biblioteca
do Spotify de quem gerou o SPOTIFY_REFRESH_TOKEN.
"""

import asyncio
import types

import pytest

import playlist_manager.telegram_handlers as th
from playlist_manager import config

DONO = 42
ESTRANHO = 99


class FakeBot:
    def __init__(self):
        self.enviadas = []

    async def send_message(self, chat_id, text):
        self.enviadas.append(text)


class FakeMessage:
    def __init__(self, texto="Playlist do Iron Maiden"):
        self.text = texto
        self.enviadas = []

    async def reply_text(self, t, reply_markup=None):
        self.enviadas.append(t)


def update_de(user_id, message=None):
    return types.SimpleNamespace(
        message=message,
        effective_user=types.SimpleNamespace(id=user_id),
        effective_chat=types.SimpleNamespace(id=user_id),
    )


@pytest.fixture
def contexto():
    return types.SimpleNamespace(user_data={}, bot=FakeBot())


@pytest.fixture
def espiao(monkeypatch):
    """Registra se o fluxo real chegou a rodar."""
    chamadas = []
    monkeypatch.setattr(th, "parse_request",
                        lambda t: chamadas.append(t) or ("Iron Maiden", None, None))
    monkeypatch.setattr(th, "get_recent_shows", lambda a, c, y, lim: [])
    return chamadas


def test_lista_vazia_mantem_o_bot_aberto(monkeypatch, contexto, espiao):
    monkeypatch.setattr(th, "ALLOWED_TELEGRAM_IDS", frozenset())
    msg = FakeMessage()
    asyncio.run(th.handle_text(update_de(ESTRANHO, msg), contexto))
    assert espiao, "o fluxo deveria ter rodado com a lista vazia"


def test_estranho_nao_chega_ao_fluxo(monkeypatch, contexto, espiao):
    monkeypatch.setattr(th, "ALLOWED_TELEGRAM_IDS", frozenset({DONO}))
    asyncio.run(th.handle_text(update_de(ESTRANHO, FakeMessage()), contexto))
    assert not espiao, "o pedido do estranho não podia chegar às integrações"
    assert "privado" in contexto.bot.enviadas[-1]


def test_autorizado_passa(monkeypatch, contexto, espiao):
    monkeypatch.setattr(th, "ALLOWED_TELEGRAM_IDS", frozenset({DONO}))
    msg = FakeMessage()
    asyncio.run(th.handle_text(update_de(DONO, msg), contexto))
    assert espiao
    assert contexto.bot.enviadas == []


def test_botao_tambem_e_barrado(monkeypatch, contexto):
    """O menu de escolha é outra porta de entrada, e cria playlist do mesmo jeito."""
    monkeypatch.setattr(th, "ALLOWED_TELEGRAM_IDS", frozenset({DONO}))
    tocado = []

    class FakeQuery:
        data = "show:abc:0"

        async def answer(self):
            tocado.append("answer")

    update = update_de(ESTRANHO)
    update.callback_query = FakeQuery()
    asyncio.run(th.handle_escolha(update, contexto))

    assert not tocado, "nem o answer() do botão devia rodar para um estranho"
    assert "privado" in contexto.bot.enviadas[-1]


def test_update_sem_usuario_e_barrado(monkeypatch, contexto, espiao):
    """Update de canal vem sem `from`; com allowlist ativa isso não passa."""
    monkeypatch.setattr(th, "ALLOWED_TELEGRAM_IDS", frozenset({DONO}))
    update = update_de(DONO, FakeMessage())
    update.effective_user = None
    asyncio.run(th.handle_text(update, contexto))
    assert not espiao


# ---------- leitura da variável de ambiente ----------
@pytest.mark.parametrize("bruto, esperado", [
    (None, frozenset()),
    ("", frozenset()),
    ("42", frozenset({42})),
    ("42,99", frozenset({42, 99})),
    (" 42 , 99 ", frozenset({42, 99})),
    ("42;99", frozenset({42, 99})),
    ("42,,99", frozenset({42, 99})),
    ("-1001234", frozenset({-1001234})),
])
def test_leitura_dos_ids(bruto, esperado):
    assert config._ids_permitidos(bruto) == esperado


def test_entrada_torta_nao_derruba_o_boot():
    """Um caractere a mais na variável não pode tirar o serviço do ar."""
    assert config._ids_permitidos("42, meu-id, 99") == frozenset({42, 99})
