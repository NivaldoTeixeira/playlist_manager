import json

import pytest
import requests

import setlist_utils as su


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


@pytest.fixture
def responder(monkeypatch):
    """Faz a setlist.fm responder o payload/status dados, sem tocar a rede."""
    def _responder(payload, status=200):
        monkeypatch.setattr(
            su.requests, "get", lambda *a, **k: FakeResp(payload, status)
        )
    return _responder


def setlist(songs, cidade="São Paulo", data="31-08-2025",
            venue="Espaço Unimed", artista="Good Charlotte"):
    return {
        "artist": {"name": artista},
        "eventDate": data,
        "venue": {"name": venue, "city": {"name": cidade}},
        "url": "https://setlist.fm/x",
        "sets": {"set": [{"song": songs}]},
    }


def test_pula_shows_sem_musicas(responder):
    """O bug original: o primeiro resultado costuma vir sem setlist cadastrada."""
    responder({"setlist": [
        setlist([]),
        setlist([]),
        setlist([{"name": "The Anthem"}, {"name": "Lifestyles"}]),
    ]})
    show = su.get_setlist("Good Charlotte")
    assert show is not None
    assert [s.name for s in show.songs] == ["The Anthem", "Lifestyles"]


def test_sem_nenhum_show_aproveitavel(responder):
    responder({"setlist": [setlist([]), setlist([])]})
    assert su.get_setlist("Good Charlotte") is None


def test_busca_vazia(responder):
    responder({"setlist": []})
    assert su.get_setlist("Banda Inexistente") is None


def test_404_e_ausencia_nao_erro(responder):
    """A setlist.fm devolve 404 quando a busca não casa com nada."""
    responder({}, status=404)
    assert su.get_setlist("Banda Inexistente") is None


@pytest.mark.parametrize("status", [429, 500, 503])
def test_erro_da_api_levanta(responder, status):
    """Falha da API não pode virar 'não achei nenhuma setlist'."""
    responder({}, status=status)
    with pytest.raises(su.SetlistIndisponivel):
        su.get_setlist("Good Charlotte")


def test_falha_de_rede_levanta(monkeypatch):
    def explode(*a, **k):
        raise requests.ConnectionError("sem rede")
    monkeypatch.setattr(su.requests, "get", explode)
    with pytest.raises(su.SetlistIndisponivel):
        su.get_setlist("Good Charlotte")


def test_cover_usa_artista_original(responder):
    """Procurar um cover com o nome da banda do show não acha nada no Spotify."""
    responder({"setlist": [setlist([
        {"name": "The Anthem"},
        {"name": "Helter Skelter", "cover": {"name": "The Beatles"}},
    ])]})
    show = su.get_setlist("Good Charlotte")
    assert [(s.name, s.search_artist) for s in show.songs] == [
        ("The Anthem", "Good Charlotte"),
        ("Helter Skelter", "The Beatles"),
    ]


def test_junta_todos_os_sets_e_ignora_nome_vazio(responder):
    dados = setlist([{"name": "A"}])
    dados["sets"]["set"].append(
        {"name": "Encore", "encore": 1, "song": [{"name": "B"}, {"name": None}, {}]}
    )
    responder({"setlist": [dados]})
    assert [s.name for s in su.get_setlist("X").songs] == ["A", "B"]


def test_metadados_do_show(responder):
    responder({"setlist": [setlist([{"name": "A"}])]})
    show = su.get_setlist("Good Charlotte")
    assert show.city == "São Paulo"
    assert show.date == "31/08/2025"          # a API entrega dd-MM-yyyy
    assert show.describe() == "Espaço Unimed - São Paulo, 31/08/2025"


def test_filtros_viram_parametros(monkeypatch):
    capturado = {}

    def espiao(url, headers=None, params=None, timeout=None):
        capturado.update(params)
        return FakeResp({"setlist": [setlist([{"name": "A"}])]})

    monkeypatch.setattr(su.requests, "get", espiao)
    su.get_setlist("Good Charlotte", city="São Paulo", year="2025")
    assert capturado["artistName"] == "Good Charlotte"
    assert capturado["cityName"] == "São Paulo"
    assert capturado["year"] == "2025"
