import json
import logging
from openai import OpenAI
from config import OPENAI_API_KEY

logger = logging.getLogger("playlist-bot")


class InterpretacaoIndisponivel(RuntimeError):
    """Não deu para chamar o LLM: chave, cota ou serviço fora."""


_oa_client = None


def get_openai_client() -> OpenAI:
    """Cria o client sob demanda.

    Instanciar no import faria o app inteiro morrer no boot quando OPENAI_API_KEY
    não estivesse configurada, em vez de subir e reportar o problema em /health.
    """
    global _oa_client
    if _oa_client is None:
        _oa_client = OpenAI(api_key=OPENAI_API_KEY)
    return _oa_client

# ---------- OPENAI: PARSING NATURAL ----------
def parse_request(text: str):
    """
    Usa LLM para extrair {artist, city, year} do pedido.
    Retorna uma tupla (artist, city, year), podendo ser None se não for identificado.
    """
    prompt = f"""
        Contexto: você é um assistente que extrai informações de texto; 
        Seu uso é para ajudar a criar playlists no spotify a partir de setlists de shows. 
        O usuário fornece uma mensagem com informações sobre o artista, cidade e ano do show (cidade e ano sendo opcionais). 
        
        Tarefa: Interprete e extraia do texto a seguir, enviado pelo, os campos JSON: artist, city, year (YYYY).
        Se não houver city ou year, retorne null. Não invente.
        Se não encontrar o nome da banda exato, veja se não é um apelido, abreviação comum ou erro de digitação ou possível correção automática do celular. 
        Se a chance de ser um erro for alta, tente corrigir.
        
        Formato de resposta: Retorne apenas um JSON puro, artist, city, year (YYYY), sem nenhum outro texto ou markdown.
        
        Texto: "{text}"
        """
    # Falhas de infraestrutura (chave ausente, rate limit, API fora) sobem para o
    # handler, que avisa o que houve. Engoli-las aqui faria todo pedido responder
    # "não entendi o artista", escondendo a causa real.
    try:
        resp = get_openai_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
    except Exception as e:
        logger.warning("Não consegui chamar o LLM: %s", e)
        raise InterpretacaoIndisponivel(str(e)) from e
    content = (resp.choices[0].message.content or "").strip()

    # Remove possíveis blocos de código Markdown
    if content.startswith("```") and content.endswith("```"):
        content = "\n".join(content.splitlines()[1:-1])

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("Não consegui decodificar JSON do LLM: %s", content)
        return None, None, None

    if not isinstance(data, dict):
        logger.warning("LLM devolveu JSON que não é objeto: %s", content)
        return None, None, None

    return data.get("artist") or None, data.get("city") or None, data.get("year") or None
