# Playlist Manager Bot

Bot de Telegram que monta playlists no Spotify a partir de setlists reais de shows.

Você manda uma mensagem em linguagem natural (`"Playlist do Good Charlotte, São Paulo 2025"`),
o bot extrai artista/cidade/ano, busca a setlist na [setlist.fm](https://www.setlist.fm/)
e cria a playlist na sua conta do Spotify.

## Como funciona

```
Telegram ──POST /webhook/<segredo>──▶ FastAPI (main.py)
                                          │
                                          ▼
                              update_queue do python-telegram-bot
                                          │
                                          ▼
                              telegram_handlers.handle_text
                                          │
              ┌───────────────────────────┼───────────────────────────┐
              ▼                           ▼                           ▼
      openai_utils.py              setlist_utils.py           spotify_utils.py
   extrai artist/city/year       busca a setlist do show    cria a playlist e
   do texto (gpt-4o-mini)          (API setlist.fm)         adiciona as faixas
```

O webhook apenas **enfileira** o update e responde `ok` na hora. O processamento roda
em segundo plano, porque criar a playlist demora mais que o timeout do Telegram — se
a resposta atrasasse, o Telegram reenviaria o mesmo update e criaria playlist duplicada.

| Arquivo | Responsabilidade |
|---|---|
| `main.py` | App FastAPI, rotas e ciclo de vida do bot |
| `config.py` | Leitura das variáveis de ambiente |
| `telegram_handlers.py` | Comandos e mensagens do Telegram |
| `openai_utils.py` | Extração de artista/cidade/ano via LLM |
| `setlist_utils.py` | Consulta à API da setlist.fm |
| `spotify_utils.py` | OAuth do Spotify e criação da playlist |

## Variáveis de ambiente

Todas são obrigatórias. Veja [`.env.example`](.env.example) para o formato.

| Variável | Onde conseguir |
|---|---|
| `TELEGRAM_TOKEN` | [@BotFather](https://t.me/BotFather) no Telegram |
| `TELEGRAM_WEBHOOK_SECRET` | Você inventa. Compõe a URL do webhook, então use algo longo e aleatório |
| `SPOTIPY_CLIENT_ID` / `SPOTIPY_CLIENT_SECRET` | [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) |
| `SPOTIPY_REDIRECT_URI` | `https://SEU-APP.onrender.com/callback` — precisa estar cadastrada no dashboard do Spotify |
| `SPOTIFY_REFRESH_TOKEN` | Gerado uma única vez pelo fluxo `/login` (veja abaixo) |
| `SETLIST_KEY` | [API da setlist.fm](https://www.setlist.fm/settings/api) |
| `OPENAI_API_KEY` | [OpenAI Platform](https://platform.openai.com/api-keys) |

O serviço **sobe mesmo com variáveis faltando** — ele só registra um aviso no log.
Para conferir o que está faltando, chame `GET /health`:

```json
{ "ok": false, "missing_config": ["SETLIST_KEY", "SPOTIFY_REFRESH_TOKEN"] }
```

A única exceção é o `TELEGRAM_TOKEN`: sem ele o `python-telegram-bot` se recusa a
construir o bot e o processo não inicia, com o erro
`InvalidToken: You must pass the token you received from https://t.me/Botfather!`.
Se o deploy estiver em crash-loop com essa mensagem, é essa variável que está faltando.

## Rodando localmente

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env    # preencha os valores

uvicorn main:app --reload --port 8000
```

Como o Telegram só entrega webhook em URL pública HTTPS, para testar o bot de verdade
localmente é preciso expor a porta (ex.: `ngrok http 8000`) e apontar o webhook para
essa URL. Sem isso, dá para testar as rotas HTTP normalmente:

```bash
curl localhost:8000/health
```

## Deploy no Render

O [`Procfile`](Procfile) já define o comando de start:

```
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```

1. **Build Command:** `pip install -r requirements.txt`
2. **Start Command:** o do `Procfile` (ou repita o comando acima manualmente)
3. Cadastre todas as variáveis de ambiente da tabela acima
4. Confirme a versão do Python em **Settings → Environment**, variável `PYTHON_VERSION`
   (o projeto é desenvolvido e testado no **3.11**)

As dependências estão **fixadas em versões exatas**, diretas e transitivas, no
`requirements.txt`. Isso é proposital: sem pin, um redeploy meses depois puxa versões
novas e pode quebrar o serviço sem nenhuma mudança de código. Ao atualizar uma lib,
faça isso de forma deliberada e teste antes.

### Limitações conhecidas do ciclo de vida

Dois comportamentos que valem conhecer antes de investigar um incidente:

- **O startup depende da API do Telegram.** `Application.initialize()` faz uma chamada
  `getMe`, então token revogado ou instabilidade do Telegram aborta o boot e o
  `/health` não chega a responder. Um serviço que não sobe e cujo `/health` não
  responde aponta para o Telegram, não para as outras integrações.
- **Entrega é at-most-once.** O webhook confirma o update antes de processá-lo, o que
  elimina a duplicação por timeout. O preço é o oposto: se o serviço reiniciar com um
  pedido em andamento, ele se perde e o Telegram não reenvia — o usuário precisa pedir
  de novo. Trocar isso por at-least-once exigiria fila persistente.

### Gerando o `SPOTIFY_REFRESH_TOKEN`

Feito **uma vez só** (o refresh token não expira sozinho):

1. Com o serviço no ar, acesse `https://SEU-APP.onrender.com/login`
2. Autorize o app na tela do Spotify
3. A página `/callback` mostra o `SPOTIFY_REFRESH_TOKEN`
4. Salve o valor nas variáveis de ambiente do Render e reinicie o serviço

Se o `/callback` responder que não veio `refresh_token`, é porque o Spotify reusou uma
autorização anterior. Ajuste `show_dialog=True` em `spotify_utils.make_auth_manager()`
e repita o fluxo.

### Registrando o webhook do Telegram

```bash
curl -F "url=https://SEU-APP.onrender.com/webhook/$TELEGRAM_WEBHOOK_SECRET" \
     "https://api.telegram.org/bot$TELEGRAM_TOKEN/setWebhook"
```

Para conferir se está registrado e se há erros de entrega:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_TOKEN/getWebhookInfo"
```

> O `TELEGRAM_WEBHOOK_SECRET` faz parte da URL e funciona como senha: qualquer chamada
> em `/webhook/<valor errado>` recebe `403`. Trate-o como segredo.

## Rotas

| Rota | Descrição |
|---|---|
| `GET /health` | Status do serviço e lista de variáveis de ambiente faltando |
| `GET /login` | Redireciona para a autorização do Spotify |
| `GET /callback` | Recebe o retorno do Spotify e exibe o refresh token |
| `POST /webhook/{segredo}` | Recebe updates do Telegram |
