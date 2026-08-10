import types

import pytest

import playlist_manager.integrations.llm as ou


def resposta(texto):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=texto))]
    )


class FakeClient:
    def __init__(self, texto=None, erro=None):
        self.texto, self.erro = texto, erro

    @property
    def chat(self):
        return types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        if self.erro:
            raise self.erro
        return resposta(self.texto)


@pytest.fixture
def responder(monkeypatch):
    def _responder(texto=None, erro=None):
        monkeypatch.setattr(ou, "get_openai_client", lambda: FakeClient(texto, erro))
    return _responder


def test_json_simples(responder):
    responder('{"artist":"Good Charlotte","city":"São Paulo","year":"2025"}')
    assert ou.parse_request("x") == ("Good Charlotte", "São Paulo", "2025")


def test_json_em_bloco_markdown(responder):
    responder('```json\n{"artist":"blink-182","city":null,"year":null}\n```')
    assert ou.parse_request("x") == ("blink-182", None, None)


def test_json_invalido_nao_derruba(responder):
    """LLM fora do formato é 'não entendi', não erro de infraestrutura."""
    responder("desculpe, não consegui")
    assert ou.parse_request("x") == (None, None, None)


def test_json_que_nao_e_objeto(responder):
    responder('["não", "é", "objeto"]')
    assert ou.parse_request("x") == (None, None, None)


def test_conteudo_vazio(responder):
    responder(None)
    assert ou.parse_request("x") == (None, None, None)


def test_campos_vazios_viram_none(responder):
    responder('{"artist":"Queen","city":"","year":null}')
    assert ou.parse_request("x") == ("Queen", None, None)


def test_falha_de_infra_levanta(responder):
    """Engolir isso faria todo pedido responder 'não entendi o artista'."""
    responder(erro=RuntimeError("OPENAI_API_KEY ausente"))
    with pytest.raises(ou.InterpretacaoIndisponivel):
        ou.parse_request("x")
