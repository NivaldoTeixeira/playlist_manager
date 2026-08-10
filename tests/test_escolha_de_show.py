"""Fluxo de escolha: pedido sem cidade nem ano oferece os shows recentes."""

import asyncio
import types

import pytest

import telegram_handlers as th
from setlist_utils import Show, Song


class FakeMessage:
    def __init__(self, texto="Playlist do Iron Maiden"):
        self.text = texto
        self.enviadas = []
        self.markups = []

    async def reply_text(self, t, reply_markup=None):
        self.enviadas.append(t)
        self.markups.append(reply_markup)


class FakeQuery:
    def __init__(self, data, message):
        self.data = data
        self.message = message
        self.respondida = False

    async def answer(self):
        self.respondida = True


def shows_exemplo(n=3):
    return [
        Show(artist="Iron Maiden", venue=f"Arena {i}", city=f"Cidade {i}",
             date=f"0{i + 1}/01/2025",
             songs=[Song("Fear of the Dark", "Iron Maiden"), Song(f"Extra {i}", "Iron Maiden")])
        for i in range(n)
    ]


@pytest.fixture
def integracoes(monkeypatch):
    """Substitui as três integrações; devolve o registro das chamadas."""
    chamadas = {"playlists": []}

    monkeypatch.setattr(th, "parse_request", lambda t: ("Iron Maiden", None, None))
    monkeypatch.setattr(th, "get_recent_shows", lambda a, c, y, lim: shows_exemplo())
    monkeypatch.setattr(th, "get_setlist", lambda a, c, y: shows_exemplo()[0])

    def criar(show, nome):
        chamadas["playlists"].append((show, nome))
        return "http://sp/p1", len(show.songs), []

    monkeypatch.setattr(th, "create_playlist_with_songs", criar)
    return chamadas


def texto(msg, context):
    asyncio.run(th.handle_text(types.SimpleNamespace(message=msg), context))


def clique(data, msg, context):
    query = FakeQuery(data, msg)
    asyncio.run(th.handle_escolha(types.SimpleNamespace(callback_query=query), context))
    return query


@pytest.fixture
def context():
    return types.SimpleNamespace(user_data={})


# ---------- oferta das opções ----------
def test_pedido_aberto_oferece_menu(integracoes, context):
    msg = FakeMessage()
    texto(msg, context)

    assert "3 shows mais recentes" in msg.enviadas[-1]
    teclado = msg.markups[-1].inline_keyboard
    assert len(teclado) == 4                       # 3 shows + a média
    assert "Setlist média" in teclado[0][0].text
    assert teclado[1][0].callback_data == "show:0"
    # Nenhuma playlist criada ainda: o usuário ainda vai escolher.
    assert integracoes["playlists"] == []


def test_botoes_mostram_data_cidade_e_tamanho(integracoes, context):
    msg = FakeMessage()
    texto(msg, context)
    rotulo = msg.markups[-1].inline_keyboard[1][0].text
    assert "01/01/2025" in rotulo and "Cidade 0" in rotulo and "(2)" in rotulo


def test_callback_data_cabe_no_limite_do_telegram(integracoes, context):
    msg = FakeMessage()
    texto(msg, context)
    for linha in msg.markups[-1].inline_keyboard:
        assert len(linha[0].callback_data.encode()) <= 64


def test_pedido_com_cidade_ou_ano_nao_pergunta(monkeypatch, integracoes, context):
    """Pedido específico continua indo direto ao ponto."""
    monkeypatch.setattr(th, "parse_request", lambda t: ("Iron Maiden", "São Paulo", "2024"))
    msg = FakeMessage()
    texto(msg, context)

    assert all(m is None for m in msg.markups)
    assert len(integracoes["playlists"]) == 1


def test_sem_shows_nao_oferece_menu(monkeypatch, integracoes, context):
    monkeypatch.setattr(th, "get_recent_shows", lambda a, c, y, lim: [])
    msg = FakeMessage()
    texto(msg, context)
    assert "Não achei nenhuma setlist" in msg.enviadas[-1]


# ---------- escolha de um show ----------
def test_escolher_show_cria_a_playlist(integracoes, context):
    msg = FakeMessage()
    texto(msg, context)
    query = clique("show:1", msg, context)

    assert query.respondida, "o botão precisa ser respondido para sair do 'carregando'"
    show, nome = integracoes["playlists"][0]
    assert show.date == "02/01/2025"
    assert "Cidade 1" in nome
    assert "http://sp/p1" in msg.enviadas[-1]


def test_indice_invalido(integracoes, context):
    msg = FakeMessage()
    texto(msg, context)
    clique("show:99", msg, context)
    assert "Não reconheci essa escolha" in msg.enviadas[-1]
    assert integracoes["playlists"] == []


def test_lista_expirada_apos_restart(integracoes, context):
    """user_data vive em memória; no plano gratuito o serviço reinicia sozinho."""
    msg = FakeMessage()
    clique("show:0", msg, context)          # sem handle_text antes
    assert "expirou" in msg.enviadas[-1]
    assert integracoes["playlists"] == []


# ---------- setlist média ----------
def test_escolher_media(integracoes, context):
    msg = FakeMessage()
    texto(msg, context)
    clique("media", msg, context)

    show, nome = integracoes["playlists"][0]
    assert nome == "Setlist média Iron Maiden"
    # "Fear of the Dark" está nos 3 shows; cada "Extra i" está em só um.
    assert [s.name for s in show.songs] == ["Fear of the Dark"]
    assert "http://sp/p1" in msg.enviadas[-1]


def test_media_sem_repertorio_comum_avisa(monkeypatch, integracoes, context):
    sem_comum = [
        Show(artist="X", date=f"0{i}/01/2025", songs=[Song(f"S{i}", "X")])
        for i in range(4)
    ]
    monkeypatch.setattr(th, "get_recent_shows", lambda a, c, y, lim: sem_comum)
    msg = FakeMessage()
    texto(msg, context)
    clique("media", msg, context)

    assert "repertório em comum" in msg.enviadas[-1]
    assert integracoes["playlists"] == []
