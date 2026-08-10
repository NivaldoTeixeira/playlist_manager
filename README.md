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

### Como o show é escolhido

A busca na setlist.fm devolve vários shows, e é comum os primeiros virem **sem
músicas registradas** (show cancelado, ou que ninguém cadastrou ainda). O bot
descarta esses e trabalha só com os que têm setlist de verdade.

O que acontece depois depende do pedido:

- **Pedido específico** (`Playlist do Iron Maiden, São Paulo 2024`) — usa direto o
  show mais relevante e já monta a playlist.
- **Pedido aberto** (`Playlist do Iron Maiden`) — responde com os **10 shows mais
  recentes** em botões, e o usuário escolhe um deles ou a **setlist média**.

### A setlist média

O site da setlist.fm mostra a "average setlist" de um artista, mas **a API 1.0 não
expõe isso** — só busca de setlists, artistas, cidades e venues. Então o cálculo é
feito aqui, em `average_setlist()`, sobre os mesmos 10 shows já baixados:

- entram as músicas presentes em pelo menos **metade** dos shows (`FREQUENCIA_MINIMA`);
- conta em quantos **shows** a música apareceu, não quantas vezes foi tocada — bis e
  medley na mesma noite não contam dobrado;
- a comparação ignora maiúsculas, e se um show anotou o artista original de um cover
  e outro não, vale a anotação, senão a busca no Spotify procuraria pela banda errada.

A ordem em que a média sai daqui é apenas informativa (da mais recorrente para a
menos). Quem decide a ordem da playlist é sempre **[A ordem da playlist](#a-ordem-da-playlist)**,
logo abaixo.

Se os shows não tiverem repertório em comum suficiente, o bot avisa e sugere escolher
um show específico, em vez de devolver playlist vazia.

> A lista de shows fica em `context.user_data`, que vive em memória. Se o serviço
> reiniciar entre a pergunta e o clique — comum no plano gratuito do Render — o bot
> responde que a lista expirou e pede o pedido de novo.

### A ordem da playlist

A playlist **não segue a ordem do show** — isso entregaria o roteiro de quem ainda vai
ao concerto. A ordenação usa um critério em cascata (`_ordenar()` em `spotify_utils.py`):

1. **Popularidade no Spotify** (campo `popularity`, 0–100, baseado no total de
   reproduções e em quão recentes elas são) — da mais tocada para a menos;
2. **Quantas vezes a música apareceu** nos shows considerados — relevante na setlist
   média, onde uma música de todo show vem antes de uma ocasional;
3. **Ordem alfabética.**

A chave de ordenação é composta, então a cascata acontece naturalmente: sem
popularidade todas empatam e o número de aparições decide; num show único todas têm
uma aparição e sobra o nome.

### Como as faixas são encontradas

Nome de música em setlist raramente casa de primeira com o catálogo do Spotify, então
a busca vai do mais específico ao mais tolerante e para no primeiro acerto:

1. `track:"nome exato" artist:"artista"`
2. `track:"nome limpo" artist:"artista"` — sem sufixos como `- Live`, `(Remastered)`, `(Ao Vivo)`
3. `nome limpo artista` — texto livre, tolera pontuação e grafia diferentes
4. `nome limpo` — último recurso

Entre os resultados o bot prefere o de artista correspondente, para não trazer versão
de tributo ou karaokê. **Covers** usam o artista original da música, não a banda do
show. Músicas repetidas (bis, medley) entram uma vez só, e as que não foram
encontradas são listadas na resposta em vez de sumirem caladas.

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
4. A versão do Python vem do [`.python-version`](.python-version) (**3.11**), que o Render lê
   automaticamente. O mesmo arquivo define a versão usada no CI, então os três ambientes
   — local, CI e produção — não divergem sozinhos.

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

## Testes

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

Rodam sem chaves de API e sem rede — todas as integrações são substituídas. Cobrem
seleção do show, covers, cascata de busca no Spotify, deduplicação, mensagens do bot
e as rotas HTTP.

## Quando alguma coisa para de funcionar

O bot depende de quatro serviços externos, e as falhas mais comuns são de credencial
ou configuração, não de código. Comece por `GET /health` e siga daqui.

### O bot não responde nada

Sintoma típico de quem volta ao projeto depois de um tempo. Se `/health` responde,
o app está no ar e o problema é o Telegram não saber para onde entregar. Confira:

```
https://api.telegram.org/bot<TOKEN>/getWebhookInfo
```

Se `url` estiver vazia, ou apontando para um endereço antigo do Render, registre de novo:

```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://SEU-APP.onrender.com/webhook/<SECRET>&drop_pending_updates=true
```

O `<SECRET>` é o `TELEGRAM_WEBHOOK_SECRET` — se não bater exatamente, o app devolve
`403` e o Telegram desiste. O `drop_pending_updates=true` descarta mensagens
represadas, que senão seriam todas entregues de uma vez no primeiro acerto.

> O webhook não some sozinho, mas some **se alguém chamar `getUpdates` nesse bot** —
> o Telegram remove o webhook automaticamente nesse caso. Trocar a URL do serviço no
> Render também exige registrar de novo.

### "Achei o show, mas não consegui falar com o Spotify"

O `SPOTIFY_REFRESH_TOKEN` venceu ou foi revogado. Refaça o fluxo de `/login`
descrito acima, salve o novo valor no Render e espere o serviço voltar a **Live**
antes de testar — o processo antigo continua no ar com a credencial velha durante
o redeploy.

### Onde ler o erro real

Painel do Render → serviço → **Logs**. As mensagens úteis:

| Log | Significado |
|---|---|
| `Ignorei N show(s) sem músicas registradas.` | Normal: a busca ignorou shows sem setlist cadastrada |
| `Usando setlist de <show> (N músicas).` | Show escolhido |
| `Não achei no Spotify: ...` | Faixas sem correspondência no catálogo |
| `Não consegui renovar o token do Spotify` | Refresh token vencido |
| `Não consegui falar com a setlist.fm` / `Setlist.fm erro <status>` | O serviço externo falhou, não o bot |
| `Busca falhou no Spotify` / `Falha ao criar a playlist` | O Spotify recusou a chamada (escopo, cota, credencial) |

## Rotas

| Rota | Descrição |
|---|---|
| `GET /health` | Status do serviço e lista de variáveis de ambiente faltando |
| `GET /login` | Redireciona para a autorização do Spotify |
| `GET /callback` | Recebe o retorno do Spotify e exibe o refresh token |
| `POST /webhook/{segredo}` | Recebe updates do Telegram |
