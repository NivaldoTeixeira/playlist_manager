"""A mensagem viaja com o erro, então o handler não precisa saber de quem é a culpa."""

import pytest

from playlist_manager import messages
from playlist_manager.errors import (
    InterpretacaoIndisponivel,
    ServicoIndisponivel,
    SetlistIndisponivel,
    SpotifyIndisponivel,
    mensagem_de,
)


@pytest.mark.parametrize("exc, esperado", [
    (InterpretacaoIndisponivel("x"), messages.ERRO_LLM),
    (SetlistIndisponivel("x"), messages.ERRO_SETLIST),
    (SpotifyIndisponivel("x"), messages.ERRO_SPOTIFY),
    (ServicoIndisponivel("x"), messages.ERRO_GENERICO),
])
def test_cada_servico_tem_sua_mensagem(exc, esperado):
    assert mensagem_de(exc) == esperado


def test_erro_inesperado_cai_no_generico():
    assert mensagem_de(ValueError("qualquer coisa")) == messages.ERRO_GENERICO


def test_detalhe_interno_nao_vaza():
    """str(exc) na conversa levaria mensagem de biblioteca para o usuário."""
    assert "segredo-interno" not in mensagem_de(KeyError("segredo-interno"))
    assert "segredo-interno" not in mensagem_de(SpotifyIndisponivel("segredo-interno"))


def test_mensagens_sao_distintas():
    """Duas iguais fariam o usuário reagir errado — esperar em vez de renovar a credencial."""
    textos = [messages.ERRO_LLM, messages.ERRO_SETLIST,
              messages.ERRO_SPOTIFY, messages.ERRO_GENERICO]
    assert len(set(textos)) == len(textos)


@pytest.mark.parametrize("classe", [
    InterpretacaoIndisponivel, SetlistIndisponivel, SpotifyIndisponivel,
])
def test_hierarquia_permite_um_except_so(classe):
    with pytest.raises(ServicoIndisponivel):
        raise classe("x")
