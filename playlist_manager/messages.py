"""Tudo que o bot fala, num lugar só.

Antes os textos ficavam inline nos handlers, e alguns apareciam duas vezes — o
que fazia uma correção de tom pegar só metade dos casos. Aqui também é o único
arquivo a mexer para ajustar a voz do bot ou traduzi-lo.

Os textos com dados variáveis são funções, não f-strings espalhadas pelo fluxo:
assim o handler fica com a decisão e este módulo com a redação.
"""

from playlist_manager.models import Show

# ---------- conversa ----------
BOAS_VINDAS = (
    "🎵 Oi! Qual playlist quer criar? Me fale o nome da banda, a cidade e ano "
    "do show que monto pra vc. \n"
    "Ex: 'Playlist do Good Charlotte, São Paulo 2025'\n\n"
    "Se não disser cidade nem ano, eu te mostro os últimos shows para escolher — "
    "ou monto a setlist média do artista."
)

NAO_AUTORIZADO = "Esse bot é privado 🙃 Fala com o dono se quiser acesso."

PROCURANDO = "Deixa eu ver o que eu acho... 🔎"

ARTISTA_NAO_ENTENDIDO = "Não entendi o artista... Confere o nome e tenta de novo, pfvr?"

SEM_SETLIST = "Não achei nenhuma setlist 😬"

NADA_NO_SPOTIFY = "Não encontrei nenhuma dessas músicas no Spotify... Sorry 😬"

ESCOLHA_NAO_RECONHECIDA = "Não reconheci essa escolha 😅 Manda o pedido de novo?"

LISTA_EXPIROU = "Essa lista expirou 😅 Manda o pedido de novo que eu busco os shows."

SEM_REPERTORIO_COMUM = (
    "Esses shows não têm repertório em comum suficiente para uma média. "
    "Escolhe um show específico?"
)

# ---------- erros ----------
# Cada serviço tem o seu: "deu erro" genérico não diz ao usuário se ele espera,
# tenta outro nome ou avisa o dono que a credencial venceu.
ERRO_GENERICO = "Deu erro aqui do meu lado... tenta de novo daqui a pouco? 😬"

ERRO_LLM = (
    "Não consegui interpretar seu pedido agora — o serviço de IA não respondeu. "
    "Tenta de novo daqui a pouco? 😬"
)

ERRO_SETLIST = (
    "A setlist.fm não está respondendo agora, então não consigo buscar o show. "
    "Tenta de novo daqui a pouco? 😬"
)

ERRO_SPOTIFY = (
    "Não consegui falar com o Spotify — a autorização pode ter vencido. "
    "Se persistir, refaça o /login e atualize o SPOTIFY_REFRESH_TOKEN. 😬"
)

# ---------- textos com dados ----------
# Quantos faltantes listar antes de resumir com "e mais N".
FALTANTES_LISTADOS = 5


def achei_o_show(show: Show) -> str:
    return f"Achei: {show.describe()} ({len(show.songs)} músicas)."


def escolheu_o_show(show: Show) -> str:
    return f"Beleza: {show.describe()} ({len(show.songs)} músicas)."


def criando_playlist(nome: str) -> str:
    return f"Booa, criando “{nome}” no Spotify..."


def escolha_um_show(artist: str, quantos: int) -> str:
    return (
        f"Achei os {quantos} shows mais recentes do {artist}. "
        "Escolhe um, ou pega a setlist média:"
    )


def media_montada(artist: str, musicas: int, shows: int) -> str:
    return (
        f"Setlist média do {artist}: {musicas} músicas que aparecem "
        f"na maioria dos últimos {shows} shows."
    )


def _lista_curta(nomes: list[str]) -> str:
    """Os primeiros nomes, resumindo o resto: a mensagem não pode virar um paredão."""
    amostra = ", ".join(nomes[:FALTANTES_LISTADOS])
    sobra = len(nomes) - FALTANTES_LISTADOS
    return f"{amostra} e mais {sobra}" if sobra > 0 else amostra


def playlist_pronta(
    url: str, adicionadas: int, faltando: list[str], nao_verificadas: list[str] = ()
) -> str:
    """O link e, se for o caso, o que não entrou.

    Listar o que ficou de fora existe porque antes essas músicas sumiam caladas e
    a playlist vinha menor que a setlist sem explicação nenhuma.

    As duas listas saem em frases diferentes: uma diz que o Spotify não tem a
    música, a outra que a busca falhou. Só a segunda vale tentar de novo.
    """
    partes = [f"Tá na mão ({adicionadas} músicas): {url}"]
    if faltando:
        partes.append(f"Não achei no Spotify: {_lista_curta(faltando)}.")
    if nao_verificadas:
        partes.append(
            f"O Spotify falhou ao procurar: {_lista_curta(list(nao_verificadas))}. "
            "Manda o pedido de novo daqui a pouco que essas talvez entrem."
        )
    return "\n\n".join(partes)
