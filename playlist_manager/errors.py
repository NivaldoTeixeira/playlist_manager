"""As falhas de serviço externo, cada uma carregando a mensagem que o usuário lê.

Antes cada integração definia a sua exceção e o handler do Telegram decidia o
texto numa cadeia de `isinstance` — o que obrigava a camada de transporte a
importar as três integrações só para escolher uma frase, e a ser editada toda
vez que entrasse um serviço novo.

Agora a mensagem viaja junto com o erro: quem levanta sabe o que aconteceu e diz
o que o usuário pode fazer a respeito. Distinguir os serviços importa — "espera
um pouco" e "a credencial venceu" pedem reações diferentes.
"""

from playlist_manager import messages


class ServicoIndisponivel(RuntimeError):
    """Um serviço externo falhou. Diferente de não haver resultado.

    Levantar isto significa "não deu para tentar", não "não existe" — mandar o
    usuário tentar outro nome com a API fora só rende tentativa inútil.
    """

    mensagem = messages.ERRO_GENERICO


class InterpretacaoIndisponivel(ServicoIndisponivel):
    """Não deu para chamar o LLM: chave, cota ou serviço fora."""

    mensagem = messages.ERRO_LLM


class SetlistIndisponivel(ServicoIndisponivel):
    """A setlist.fm não respondeu. Diferente de não existir show cadastrado."""

    mensagem = messages.ERRO_SETLIST


class SpotifyIndisponivel(ServicoIndisponivel):
    """Não deu para falar com o Spotify: credencial, cota ou serviço fora."""

    mensagem = messages.ERRO_SPOTIFY


def mensagem_de(exc: BaseException) -> str:
    """O texto a mostrar para esta falha.

    Erro inesperado cai no genérico de propósito: `str(exc)` poderia levar
    detalhe interno para a conversa.
    """
    if isinstance(exc, ServicoIndisponivel):
        return exc.mensagem
    return messages.ERRO_GENERICO
