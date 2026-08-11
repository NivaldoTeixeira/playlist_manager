"""Testes das rotas HTTP.

O TestClient é usado sem `with`: entrar no contexto dispara o lifespan, que chama
`getMe` na API do Telegram — os testes não fazem rede. O bot é montado à mão em
`app.state`, que é justamente o que `build_telegram_app()` permite: montar sem
inicializar.
"""

import pytest
from fastapi.testclient import TestClient

from playlist_manager import main


@pytest.fixture
def client():
    main.app.state.tg_app = main.build_telegram_app()
    return TestClient(main.app)


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "missing_config": []}


def test_health_lista_o_que_falta(client, monkeypatch):
    monkeypatch.setattr(main, "missing_config", lambda: ["SETLIST_KEY"])
    r = client.get("/health")
    assert r.json() == {"ok": False, "missing_config": ["SETLIST_KEY"]}


def test_login_redireciona_para_o_spotify(client):
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"].startswith("https://accounts.spotify.com/authorize")


def test_callback_sem_code(client):
    r = client.get("/callback")
    assert r.status_code == 400


def test_callback_com_erro_do_spotify(client):
    r = client.get("/callback", params={"error": "access_denied"})
    assert r.status_code == 400
    assert "access_denied" in r.text


def test_webhook_rejeita_segredo_errado(client):
    assert client.post("/webhook/errado", json={}).status_code == 403


def test_webhook_enfileira_e_responde_na_hora(client):
    """Processar antes de responder estouraria o timeout do Telegram."""
    antes = main.app.state.tg_app.update_queue.qsize()
    payload = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": 1700000000,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False, "first_name": "NT"},
            "text": "Playlist do Good Charlotte",
        },
    }
    r = client.post(f"/webhook/{main.WEBHOOK_SECRET}", json=payload)
    assert r.status_code == 200
    assert main.app.state.tg_app.update_queue.qsize() == antes + 1


def test_webhook_antes_do_bot_pronto(client):
    """503 e não 500: assim o Telegram tenta de novo em vez de descartar o update."""
    if hasattr(main.app.state, "tg_app"):
        del main.app.state.tg_app
    r = client.post(f"/webhook/{main.WEBHOOK_SECRET}", json={"update_id": 1})
    assert r.status_code == 503


def test_bot_processa_updates_em_paralelo():
    """Com o padrão do PTB (1) os pedidos viram fila e a trava de toque duplo não teria função."""
    assert main.build_telegram_app().concurrent_updates > 1


def test_sobe_sem_telegram_token(monkeypatch):
    """Morrer no boot esconderia justamente o /health que diz o que falta."""
    monkeypatch.setattr(main, "TELEGRAM_TOKEN", "")
    if hasattr(main.app.state, "tg_app"):
        del main.app.state.tg_app

    # O `with` dispara o lifespan; sem token ele não deve chamar a API do Telegram.
    with TestClient(main.app) as cliente:
        assert cliente.get("/health").status_code == 200
        assert cliente.post(f"/webhook/{main.WEBHOOK_SECRET}", json={}).status_code == 503


def test_callback_nao_reaproveita_token_do_cache(client, monkeypatch):
    """A rota é usada quando o token velho não serve mais; devolvê-lo erraria o alvo."""
    chamadas = {}

    class FakeAuth:
        def get_access_token(self, code, as_dict=True, check_cache=True):
            chamadas["check_cache"] = check_cache
            return {"refresh_token": "token-novo"}

    monkeypatch.setattr(main, "make_auth_manager", FakeAuth)
    r = client.get("/callback", params={"code": "abc"})

    assert chamadas["check_cache"] is False
    assert "token-novo" in r.text
