import json
import logging
from openai import OpenAI
from config import OPENAI_API_KEY

logger = logging.getLogger("playlist-bot")
oa_client = OpenAI(api_key=OPENAI_API_KEY)

# ---------- OPENAI: PARSING NATURAL ----------
def parse_request(text: str):
    """
    Usa LLM para extrair {artist, city, year} do pedido.
    Retorna uma tupla (artist, city, year), podendo ser None se não for identificado.
    """
    import json
    try:
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
        resp = oa_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        content = resp.choices[0].message.content.strip()

        # Remove possíveis blocos de código Markdown
        if content.startswith("```") and content.endswith("```"):
            content = "\n".join(content.splitlines()[1:-1])

        data = json.loads(content)
        artist = data.get("artist") or None
        city = data.get("city") or None
        year = data.get("year") or None
        return artist, city, year

    except json.JSONDecodeError:
        logger.warning("Não consegui decodificar JSON do LLM: %s", content)
        return None, None, None
    except Exception as e:
        logger.warning("Erro no parse_request: %s", e)
        return None, None, None
