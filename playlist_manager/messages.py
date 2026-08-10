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


def playlist_pronta(url: str, adicionadas: int, faltando: list[str]) -> str:
    """O link e, se for o caso, o que não entrou.

    Listar os faltantes existe porque antes eles sumiam calados e a playlist
    vinha menor que a setlist sem explicação nenhuma.
    """
    resposta = f"Tá na mão ({adicionadas} músicas): {url}"
    if not faltando:
        return resposta

    amostra = ", ".join(faltando[:FALTANTES_LISTADOS])
    sobra = len(faltando) - FALTANTES_LISTADOS
    resto = f" e mais {sobra}" if sobra > 0 else ""
    return f"{resposta}\n\nNão achei no Spotify: {amostra}{resto}."
