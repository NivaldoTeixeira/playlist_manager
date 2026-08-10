import pytest
import spotipy

import spotify_utils as sp_u
from setlist_utils import Show, Song


def faixa(tid, artista):
    return {"id": tid, "artists": [{"name": artista}]}


class FakeSpotify:
    """Responde só às consultas listadas em `catalogo`, registrando as tentativas."""

    def __init__(self, catalogo=None, erro=None):
        self.catalogo = catalogo or {}
        self.erro = erro
        self.tentativas = []
        self.criadas = []
        self.adicionadas = []

    def search(self, q, limit=10, type="track"):
        self.tentativas.append(q)
        if self.erro:
            raise self.erro
        return {"tracks": {"items": self.catalogo.get(q, [])}}

    def current_user(self):
        return {"id": "eu"}

    def user_playlist_create(self, user, name, public, description):
        self.criadas.append({"name": name, "description": description})
        return {"id": "p1", "external_urls": {"spotify": "http://sp/p1"}}

    def playlist_add_items(self, pid, ids):
        self.adicionadas.extend(ids)


@pytest.fixture
def usar(monkeypatch):
    def _usar(fake):
        monkeypatch.setattr(sp_u, "get_spotify_client", lambda: fake)
        return fake
    return _usar


# ---------- limpeza de nome ----------
@pytest.mark.parametrize("bruto,limpo", [
    ("The Anthem", "The Anthem"),
    ("The Anthem - Live", "The Anthem"),
    ("Lifestyles (Remastered 2010)", "Lifestyles"),
    ("Song (Live at Wembley)", "Song"),
    ("Dammit [Live]", "Dammit"),
    ("Vida (Ao Vivo)", "Vida"),
    ("Bohemian Rhapsody", "Bohemian Rhapsody"),
    # Não pode comer título legítimo que contém as palavras-chave.
    ("Live and Let Die", "Live and Let Die"),
    ("Single Ladies", "Single Ladies"),
])
def test_limpar(bruto, limpo):
    assert sp_u._limpar(bruto) == limpo


def test_limpar_nao_devolve_vazio():
    """Título que é só ruído deve sobrar como estava, não virar string vazia."""
    assert sp_u._limpar("- Live") != ""


# ---------- montagem das consultas ----------
def test_aspas_nao_quebram_a_query():
    """Aspas no termo fechariam track:"..." cedo e a música sumiria."""
    for q in sp_u._consultas(Song("“Heroes”", "David Bowie")):
        assert q.count('"') % 2 == 0
    assert 'track:"Heroes" artist:"David Bowie"' in sp_u._consultas(Song("“Heroes”", "David Bowie"))


def test_apostrofo_preservado():
    assert sp_u._consultas(Song("Don’t Stop", "Queen"))[0] == 'track:"Don\'t Stop" artist:"Queen"'


def test_consultas_sem_repeticao():
    consultas = sp_u._consultas(Song("Dammit", "blink-182"))
    assert len(consultas) == len(set(consultas))


# ---------- busca ----------
def test_cai_para_texto_livre():
    fake = FakeSpotify({"The Anthem Good Charlotte": [faixa("t1", "Good Charlotte")]})
    assert sp_u._buscar_faixa(fake, Song("The Anthem - Live", "Good Charlotte")) == "t1"


def test_prefere_artista_correspondente():
    """Consulta ampla pode trazer tributo/karaokê antes do original."""
    fake = FakeSpotify({'track:"Dammit" artist:"blink-182"': [
        faixa("karaoke", "Karaoke Band"),
        faixa("certo", "blink-182"),
    ]})
    assert sp_u._buscar_faixa(fake, Song("Dammit", "blink-182")) == "certo"


def test_sem_match_devolve_none():
    fake = FakeSpotify({})
    assert sp_u._buscar_faixa(fake, Song("Inexistente", "X")) is None


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_erro_sistemico_sobe_na_hora(status):
    """401/429/5xx afetam tudo: insistir só queima cota e atrasa o erro real."""
    fake = FakeSpotify(erro=spotipy.SpotifyException(status, -1, "boom"))
    with pytest.raises(sp_u.SpotifyIndisponivel):
        sp_u._buscar_faixa(fake, Song("A", "B"))
    assert len(fake.tentativas) == 1


def test_falha_pontual_nao_vira_musica_ausente():
    fake = FakeSpotify(erro=ValueError("timeout"))
    with pytest.raises(sp_u.BuscaIndisponivel):
        sp_u._buscar_faixa(fake, Song("A", "B"))


# ---------- criação da playlist ----------
def test_cria_playlist_e_reporta_faltantes(usar):
    fake = usar(FakeSpotify({
        'track:"The Anthem" artist:"GC"': [faixa("t1", "GC")],
        'track:"Lifestyles" artist:"GC"': [faixa("t2", "GC")],
    }))
    show = Show(artist="GC", venue="Espaço Unimed", city="São Paulo", date="31/08/2025",
                songs=[Song("The Anthem", "GC"), Song("Lifestyles", "GC"), Song("Sumida", "GC")])

    url, adicionadas, faltando = sp_u.create_playlist_with_songs(show, "Setlist GC")

    assert url == "http://sp/p1"
    assert adicionadas == 2 == len(fake.adicionadas)
    assert faltando == ["Sumida"]
    assert "São Paulo" in fake.criadas[0]["description"]


def test_repetida_conta_uma_vez_e_busca_uma_vez(usar):
    """Bis/medley repetem a música; não pode duplicar nem rebuscar."""
    fake = usar(FakeSpotify({'track:"X" artist:"GC"': [faixa("t1", "GC")]}))
    show = Show(artist="GC", songs=[Song("X", "GC")] * 3)

    url, adicionadas, faltando = sp_u.create_playlist_with_songs(show, "P")

    assert adicionadas == len(fake.adicionadas) == 1
    assert faltando == []
    assert len(fake.tentativas) == 1


def test_repetida_ausente_listada_uma_vez(usar):
    fake = usar(FakeSpotify({}))
    show = Show(artist="GC", songs=[Song("X", "GC")] * 3)
    url, adicionadas, faltando = sp_u.create_playlist_with_songs(show, "P")
    assert faltando == ["X"]
    assert url is None


def test_nao_cria_playlist_vazia(usar):
    fake = usar(FakeSpotify({}))
    show = Show(artist="GC", songs=[Song("A", "GC")])
    url, adicionadas, _ = sp_u.create_playlist_with_songs(show, "P")
    assert (url, adicionadas) == (None, 0)
    assert fake.criadas == []


def test_tudo_indisponivel_levanta(usar):
    """Spotify fora não pode virar 'nenhuma dessas músicas existe'."""
    usar(FakeSpotify(erro=ValueError("timeout")))
    show = Show(artist="GC", songs=[Song("A", "GC")])
    with pytest.raises(sp_u.SpotifyIndisponivel):
        sp_u.create_playlist_with_songs(show, "P")


def test_falha_ao_criar_playlist_vira_spotify_indisponivel(usar):
    """403 de app em modo de desenvolvimento acontece só na criação."""
    fake = usar(FakeSpotify({'track:"A" artist:"GC"': [faixa("t1", "GC")]}))
    fake.user_playlist_create = lambda **k: (_ for _ in ()).throw(
        spotipy.SpotifyException(403, -1, "forbidden")
    )
    show = Show(artist="GC", songs=[Song("A", "GC")])
    with pytest.raises(sp_u.SpotifyIndisponivel):
        sp_u.create_playlist_with_songs(show, "P")


def test_refresh_token_ausente(monkeypatch):
    monkeypatch.setattr(sp_u, "SPOTIFY_REFRESH_TOKEN", "")
    with pytest.raises(sp_u.SpotifyIndisponivel):
        sp_u.get_spotify_client()
