import asyncio
import time
import types

import pytest

import playlist_manager.telegram_handlers as th
from playlist_manager.errors import (
    InterpretacaoIndisponivel,
    SetlistIndisponivel,
    SpotifyIndisponivel,
)
from playlist_manager.models import Show, Song


class FakeMessage:
    def __init__(self, texto="Playlist do Good Charlotte, São Paulo 2025"):
        self.text = texto
        self.enviadas = []

    async def reply_text(self, t):
        self.enviadas.append(t)


def contexto():
    """Contexto mínimo do PTB: handle_text guarda o menu em user_data."""
    return types.SimpleNamespace(user_data={})


def show_exemplo(n=12):
    return Show(artist="Good Charlotte", venue="Espaço Unimed", city="São Paulo",
                date="31/08/2025", songs=[Song(f"M{i}", "Good Charlotte") for i in range(n)])


def rodar(monkeypatch, *, parse=None, setlist=None, playlist=None, texto=None):
    """Executa handle_text com as três integrações substituídas."""
    monkeypatch.setattr(th, "parse_request",
                        parse or (lambda t: ("Good Charlotte", "São Paulo", "2025")))
    monkeypatch.setattr(th, "get_setlist", setlist or (lambda a, c, y: show_exemplo()))
    monkeypatch.setattr(th, "create_playlist_with_songs",
                        playlist or (lambda s, n: ("http://sp/p1", 12, [])))

    msg = FakeMessage(texto) if texto else FakeMessage()
    asyncio.run(th.handle_text(types.SimpleNamespace(message=msg), contexto()))
    return msg.enviadas


def test_caminho_feliz(monkeypatch):
    enviadas = rodar(monkeypatch)
    assert "Espaço Unimed - São Paulo, 31/08/2025" in enviadas[1]
    assert "http://sp/p1" in enviadas[-1]


def test_lista_faltantes_resumindo(monkeypatch):
    faltando = ["A", "B", "C", "D", "E", "F", "G"]
    enviadas = rodar(monkeypatch, playlist=lambda s, n: ("http://sp/p1", 5, faltando))
    assert "A, B, C, D, E" in enviadas[-1]
    assert "e mais 2" in enviadas[-1]


def test_poucos_faltantes_sem_resumo(monkeypatch):
    enviadas = rodar(monkeypatch, playlist=lambda s, n: ("http://sp/p1", 10, ["A", "B"]))
    assert "A, B." in enviadas[-1]
    assert "e mais" not in enviadas[-1]


def test_artista_nao_identificado(monkeypatch):
    enviadas = rodar(monkeypatch, parse=lambda t: (None, None, None))
    assert "Não entendi o artista" in enviadas[-1]


def test_sem_setlist(monkeypatch):
    enviadas = rodar(monkeypatch, setlist=lambda a, c, y: None)
    assert "Não achei nenhuma setlist" in enviadas[-1]


def test_setlist_achada_mas_nada_no_spotify(monkeypatch):
    enviadas = rodar(monkeypatch, playlist=lambda s, n: (None, 0, ["A"]))
    assert "encontrei nenhuma dessas músicas" in enviadas[-1].lower()


# ---------- mensagens específicas por serviço ----------
def explodir(exc):
    def _explodir(*a, **k):
        raise exc
    return _explodir


def test_mensagem_especifica_llm(monkeypatch):
    enviadas = rodar(monkeypatch, parse=explodir(InterpretacaoIndisponivel("x")))
    assert "serviço de IA" in enviadas[-1]


def test_mensagem_especifica_setlist(monkeypatch):
    enviadas = rodar(monkeypatch, setlist=explodir(SetlistIndisponivel("x")))
    assert "setlist.fm não está respondendo" in enviadas[-1]


def test_mensagem_especifica_spotify(monkeypatch):
    """O caso que travou a retomada: refresh token vencido."""
    enviadas = rodar(monkeypatch, playlist=explodir(SpotifyIndisponivel("invalid_grant")))
    assert "Spotify" in enviadas[-1]
    assert "/login" in enviadas[-1]


def test_erro_inesperado_nao_vaza_detalhe(monkeypatch):
    enviadas = rodar(monkeypatch, playlist=explodir(ValueError("segredo-interno")))
    assert "segredo-interno" not in enviadas[-1]
    assert "Deu erro aqui do meu lado" in enviadas[-1]


# ---------- concorrência ----------
def test_nao_bloqueia_o_event_loop(monkeypatch):
    """As integrações são síncronas; travar o loop atrasaria /health e outros webhooks."""
    # Com cidade e ano o fluxo vai direto ao ponto, sem o menu de escolha — é o
    # caminho que encadeia as três chamadas bloqueantes.
    monkeypatch.setattr(th, "parse_request",
                        lambda t: (time.sleep(0.4) or ("Good Charlotte", "São Paulo", "2025")))
    monkeypatch.setattr(th, "get_setlist", lambda a, c, y: time.sleep(0.4) or show_exemplo())
    monkeypatch.setattr(th, "create_playlist_with_songs",
                        lambda s, n: time.sleep(0.4) or ("http://sp/p1", 12, []))

    async def cenario():
        atrasos = []

        async def batimento():
            while True:
                t0 = time.perf_counter()
                await asyncio.sleep(0.02)
                atrasos.append(time.perf_counter() - t0)

        hb = asyncio.create_task(batimento())
        msg = FakeMessage()
        await th.handle_text(types.SimpleNamespace(message=msg), contexto())
        hb.cancel()
        return atrasos, msg.enviadas

    atrasos, enviadas = asyncio.run(cenario())
    # Sem isto o teste passaria mesmo se o fluxo tivesse caído num ramo de erro
    # antes de chegar às chamadas bloqueantes.
    assert "http://sp/p1" in enviadas[-1], f"o fluxo não completou: {enviadas}"
    assert atrasos, "o batimento não chegou a rodar"
    assert max(atrasos) < 0.2, f"event loop travou por {max(atrasos):.2f}s"


@pytest.mark.asyncio
async def test_cmd_start():
    msg = FakeMessage()
    await th.cmd_start(types.SimpleNamespace(message=msg), None)
    assert "playlist" in msg.enviadas[0].lower()
