"""Fluxo de escolha: pedido sem cidade nem ano oferece os shows recentes."""

import asyncio
import types

import pytest

import playlist_manager.telegram_handlers as th
from playlist_manager.integrations.spotify import SpotifyIndisponivel
from playlist_manager.models import Show, Song

CHAT_ID = 42


class FakeMessage:
    """A mensagem do usuário: responde e registra o que foi enviado."""

    def __init__(self, texto="Playlist do Iron Maiden"):
        self.text = texto
        self.enviadas = []
        self.markups = []

    async def reply_text(self, t, reply_markup=None):
        self.enviadas.append(t)
        self.markups.append(reply_markup)


class FakeBot:
    """O callback responde pelo chat, não pela mensagem do botão."""

    def __init__(self):
        self.enviadas = []

    async def send_message(self, chat_id, text):
        self.enviadas.append(text)


class FakeQuery:
    def __init__(self, data, message=None):
        self.data = data
        self.message = message          # pode ser None: botão com mais de 48h
        self.respondida = False
        self.teclado_removido = False

    async def answer(self):
        self.respondida = True

    async def edit_message_reply_markup(self, reply_markup=None):
        if self.message is None:
            raise RuntimeError("mensagem inacessível")
        self.teclado_removido = True


def shows_exemplo(n=3, artista="Iron Maiden"):
    return [
        Show(artist=artista, venue=f"Arena {i}", city=f"Cidade {i}",
             date=f"0{i + 1}/01/2025",
             songs=[Song("Fear of the Dark", artista), Song(f"Extra {i}", artista)])
        for i in range(n)
    ]


@pytest.fixture
def context():
    return types.SimpleNamespace(user_data={}, bot=FakeBot())


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


def clique(data, context, message=None):
    query = FakeQuery(data, message)
    update = types.SimpleNamespace(
        callback_query=query,
        effective_chat=types.SimpleNamespace(id=CHAT_ID),
    )
    asyncio.run(th.handle_escolha(update, context))
    return query


def token_do_menu(msg):
    """Extrai o token embutido nos botões do último menu enviado."""
    return msg.markups[-1].inline_keyboard[0][0].callback_data.split(":")[1]


# ---------- oferta das opções ----------
def test_pedido_aberto_oferece_menu(integracoes, context):
    msg = FakeMessage()
    texto(msg, context)

    assert "3 shows mais recentes" in msg.enviadas[-1]
    teclado = msg.markups[-1].inline_keyboard
    assert len(teclado) == 4                       # 3 shows + a média
    assert "Setlist média" in teclado[0][0].text
    assert teclado[1][0].callback_data.startswith("show:")
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
    query = clique(f"show:{token_do_menu(msg)}:1", context, msg)

    assert query.respondida, "o botão precisa ser respondido para sair do 'carregando'"
    show, nome = integracoes["playlists"][0]
    assert show.date == "02/01/2025"
    assert "Cidade 1" in nome
    assert "http://sp/p1" in context.bot.enviadas[-1]


def test_indice_fora_da_lista(integracoes, context):
    msg = FakeMessage()
    texto(msg, context)
    clique(f"show:{token_do_menu(msg)}:99", context, msg)
    assert "Não reconheci essa escolha" in context.bot.enviadas[-1]
    assert integracoes["playlists"] == []


@pytest.mark.parametrize("data", ["lixo", "show:tok", "media", "show:tok:abc", ""])
def test_callback_data_torto(integracoes, context, data):
    clique(data, context, FakeMessage())
    assert "Não reconheci essa escolha" in context.bot.enviadas[-1]
    assert integracoes["playlists"] == []


def test_lista_expirada_apos_restart(integracoes, context):
    """user_data vive em memória; no plano gratuito o serviço reinicia sozinho."""
    clique("show:sumiu:0", context, FakeMessage())
    assert "expirou" in context.bot.enviadas[-1]
    assert integracoes["playlists"] == []


# ---------- menus antigos ----------
def test_menu_antigo_usa_a_lista_dele(monkeypatch, integracoes, context):
    """Botão de um pedido antigo não pode montar a playlist do pedido novo."""
    msg1 = FakeMessage()
    texto(msg1, context)
    token1 = token_do_menu(msg1)

    monkeypatch.setattr(th, "parse_request", lambda t: ("Metallica", None, None))
    monkeypatch.setattr(th, "get_recent_shows", lambda a, c, y, lim: shows_exemplo(artista="Metallica"))
    msg2 = FakeMessage("Playlist do Metallica")
    texto(msg2, context)
    assert token_do_menu(msg2) != token1

    clique(f"show:{token1}:0", context, msg1)      # clica no menu antigo

    show, nome = integracoes["playlists"][0]
    assert show.artist == "Iron Maiden"
    assert "Iron Maiden" in nome


def test_menus_antigos_sao_descartados(integracoes, context):
    """A memória por usuário não pode crescer sem limite."""
    for _ in range(th.MENUS_GUARDADOS + 3):
        texto(FakeMessage(), context)
    assert len(context.user_data["menus"]) == th.MENUS_GUARDADOS


# ---------- toque duplo ----------
def test_toque_duplo_cria_uma_playlist_so(monkeypatch, integracoes, context):
    msg = FakeMessage()
    texto(msg, context)
    data = f"show:{token_do_menu(msg)}:0"

    # Simula o segundo toque enquanto o primeiro ainda monta a playlist.
    def criar_lento(show, nome):
        clique(data, context, msg)
        integracoes["playlists"].append((show, nome))
        return "http://sp/p1", 2, []

    monkeypatch.setattr(th, "create_playlist_with_songs", criar_lento)
    clique(data, context, msg)

    assert len(integracoes["playlists"]) == 1


def test_teclado_removido_apos_escolher(integracoes, context):
    msg = FakeMessage()
    texto(msg, context)
    query = clique(f"show:{token_do_menu(msg)}:0", context, msg)
    assert query.teclado_removido


# ---------- botão velho, mensagem inacessível ----------
def test_botao_sem_mensagem_acessivel(integracoes, context):
    """CallbackQuery.message some em botão com mais de 48h; não pode virar silêncio."""
    msg = FakeMessage()
    texto(msg, context)
    clique(f"show:{token_do_menu(msg)}:0", context, message=None)

    assert "http://sp/p1" in context.bot.enviadas[-1]
    assert len(integracoes["playlists"]) == 1


# ---------- erros durante a montagem ----------
def test_falha_do_spotify_tem_mensagem_propria(monkeypatch, integracoes, context):
    """Não pode ser reportada como 'não reconheci essa escolha'."""
    msg = FakeMessage()
    texto(msg, context)

    def explode(show, nome):
        raise SpotifyIndisponivel("invalid_grant")

    monkeypatch.setattr(th, "create_playlist_with_songs", explode)
    clique(f"show:{token_do_menu(msg)}:0", context, msg)

    assert "Spotify" in context.bot.enviadas[-1]
    assert "não reconheci" not in context.bot.enviadas[-1].lower()


def test_keyerror_do_spotify_nao_vira_escolha_invalida(monkeypatch, integracoes, context):
    """A resposta do Spotify pode faltar uma chave; isso é erro, não escolha errada."""
    msg = FakeMessage()
    texto(msg, context)

    def explode(show, nome):
        raise KeyError("external_urls")

    monkeypatch.setattr(th, "create_playlist_with_songs", explode)
    clique(f"show:{token_do_menu(msg)}:0", context, msg)

    assert "Deu erro aqui do meu lado" in context.bot.enviadas[-1]


# ---------- setlist média ----------
def test_escolher_media(integracoes, context):
    msg = FakeMessage()
    texto(msg, context)
    clique(f"media:{token_do_menu(msg)}", context, msg)

    show, nome = integracoes["playlists"][0]
    assert nome == "Setlist média Iron Maiden"
    # "Fear of the Dark" está nos 3 shows; cada "Extra i" está em só um.
    assert [s.name for s in show.songs] == ["Fear of the Dark"]
    assert "http://sp/p1" in context.bot.enviadas[-1]


def test_media_sem_repertorio_comum_avisa(monkeypatch, integracoes, context):
    sem_comum = [
        Show(artist="X", date=f"0{i}/01/2025", songs=[Song(f"S{i}", "X")])
        for i in range(4)
    ]
    monkeypatch.setattr(th, "get_recent_shows", lambda a, c, y, lim: sem_comum)
    msg = FakeMessage()
    texto(msg, context)
    clique(f"media:{token_do_menu(msg)}", context, msg)

    assert "repertório em comum" in context.bot.enviadas[-1]
    assert integracoes["playlists"] == []
