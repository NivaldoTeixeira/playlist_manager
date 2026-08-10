"""O /health depende de missing_config() para dizer o que falta configurar."""

from playlist_manager import config


def test_ambiente_completo_nao_falta_nada():
    """O conftest preenche todas as obrigatórias antes de qualquer import."""
    assert config.missing_config() == []


def test_lista_o_que_falta(monkeypatch):
    monkeypatch.delenv("SETLIST_KEY")
    monkeypatch.delenv("OPENAI_API_KEY")
    assert config.missing_config() == ["OPENAI_API_KEY", "SETLIST_KEY"]


def test_variavel_vazia_conta_como_faltando(monkeypatch):
    """Cadastrar a variável sem valor no Render é um engano comum."""
    monkeypatch.setenv("SETLIST_KEY", "")
    assert config.missing_config() == ["SETLIST_KEY"]


def test_le_o_ambiente_na_hora(monkeypatch):
    """A lista congelada no import fazia o /health reclamar de algo já corrigido."""
    monkeypatch.delenv("SPOTIFY_REFRESH_TOKEN")
    assert "SPOTIFY_REFRESH_TOKEN" in config.missing_config()

    monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "novo-valor")
    assert "SPOTIFY_REFRESH_TOKEN" not in config.missing_config()


def test_ordem_alfabetica(monkeypatch):
    """A resposta do /health é lida por humano; ordem estável evita confusão."""
    monkeypatch.delenv("TELEGRAM_TOKEN")
    monkeypatch.delenv("SETLIST_KEY")
    faltando = config.missing_config()
    assert faltando == sorted(faltando)
