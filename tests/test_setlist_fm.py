import json

import pytest
import requests

import playlist_manager.integrations.setlist_fm as su


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


def test_cover_guarda_artista_original_como_alternativa(responder):
    """A banda do show continua em primeiro: se ela gravou o cover, é a versão dela."""
    responder({"setlist": [setlist([
        {"name": "The Anthem"},
        {"name": "Helter Skelter", "cover": {"name": "The Beatles"}},
    ])]})
    show = su.get_setlist("Good Charlotte")
    assert [(s.name, s.artist, s.cover_of) for s in show.songs] == [
        ("The Anthem", "Good Charlotte", None),
        ("Helter Skelter", "Good Charlotte", "The Beatles"),
    ]
    assert show.songs[1].search_artists == ("Good Charlotte", "The Beatles")


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


# ---------- shows recentes ----------
def test_get_recent_shows_filtra_e_limita(responder):
    responder({"setlist": [
        setlist([]),                                   # sem músicas: fora
        setlist([{"name": "A"}], data="01-01-2025"),
        setlist([]),                                   # sem músicas: fora
        setlist([{"name": "B"}], data="02-01-2025"),
        setlist([{"name": "C"}], data="03-01-2025"),
    ]})
    shows = su.get_recent_shows("X", limit=2)
    assert len(shows) == 2
    assert [s.date for s in shows] == ["01/01/2025", "02/01/2025"]


def test_get_recent_shows_vazio(responder):
    responder({"setlist": [setlist([]), setlist([])]})
    assert su.get_recent_shows("X") == []


def test_get_setlist_delega_para_o_primeiro(responder):
    responder({"setlist": [setlist([]), setlist([{"name": "A"}])]})
    show = su.get_setlist("X")
    assert [s.name for s in show.songs] == ["A"]


# ---------- setlist média ----------
def show_com(nomes, artista="GC"):
    return su.Show(artist=artista, songs=[su.Song(n, artista) for n in nomes])


def test_media_mantem_so_o_que_se_repete():
    """A setlist.fm calcula isso no site; a API 1.0 não expõe, então é conta nossa."""
    shows = [
        show_com(["A", "B", "raridade1"]),
        show_com(["A", "B", "raridade2"]),
        show_com(["A", "B", "raridade3"]),
        show_com(["A", "B", "raridade4"]),
    ]
    assert [s.name for s in su.average_setlist(shows)] == ["A", "B"]


def test_media_ordena_da_mais_recorrente_para_a_menos():
    """A ordem da playlist é decidida em _ordenar(); aqui só a recorrência."""
    shows = [
        show_com(["sempre", "quase_sempre"]),
        show_com(["sempre", "quase_sempre"]),
        show_com(["sempre", "quase_sempre"]),
        show_com(["sempre", "metade"]),
    ]
    assert [s.name for s in su.average_setlist(shows)] == ["sempre", "quase_sempre"]


def test_media_conta_shows_e_nao_execucoes():
    """Música tocada duas vezes na mesma noite não pode contar dobrado."""
    shows = [
        show_com(["repetida", "repetida"]),   # bis: duas execuções, um show
        show_com(["repetida", "repetida"]),
        show_com(["repetida", "repetida"]),
        show_com(["outra"]),
        show_com(["outra"]),
        show_com(["outra"]),
        show_com(["outra"]),
    ]
    media = {s.name: s.plays for s in su.average_setlist(shows)}
    # "repetida" está em 3 dos 7 shows: abaixo da metade, fica de fora.
    assert media == {"outra": 4}


def test_media_prefere_o_registro_que_identifica_o_cover():
    """Se um show anotou o artista original e outro não, vale a anotação."""
    shows = [
        su.Show(artist="GC", songs=[su.Song("Helter Skelter", "GC")]),          # sem anotação
        su.Show(artist="GC", songs=[su.Song("Helter Skelter", "GC", cover_of="The Beatles")]),
    ]
    assert su.average_setlist(shows)[0].cover_of == "The Beatles"


def test_media_agrupa_ignorando_caixa():
    shows = [show_com(["The Anthem"]), show_com(["the anthem"])]
    assert len(su.average_setlist(shows)) == 1


def test_media_de_um_show_e_o_proprio_show():
    assert [s.name for s in su.average_setlist([show_com(["A", "B"])])] == ["A", "B"]


def test_media_sem_shows():
    assert su.average_setlist([]) == []


def test_media_sem_repertorio_comum():
    """Shows sem nada em comum não rendem média — o bot precisa avisar."""
    shows = [show_com(["A"]), show_com(["B"]), show_com(["C"]), show_com(["D"])]
    assert su.average_setlist(shows) == []


def test_media_preserva_artista_de_cover():
    shows = [
        su.Show(artist="GC", songs=[su.Song("Helter Skelter", "GC", cover_of="The Beatles")]),
        su.Show(artist="GC", songs=[su.Song("Helter Skelter", "GC", cover_of="The Beatles")]),
    ]
    assert su.average_setlist(shows)[0].cover_of == "The Beatles"
