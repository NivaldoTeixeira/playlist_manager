"""Configuração comum dos testes.

As variáveis de ambiente são preenchidas com valores falsos antes de qualquer
import do projeto: os módulos leem `config` no import, e sem isso os testes
dependeriam do ambiente da máquina. Nenhum teste faz chamada de rede.
"""

import os

os.environ.setdefault("TELEGRAM_TOKEN", "123:FAKE")
os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "segredo-de-teste")
os.environ.setdefault("SPOTIPY_CLIENT_ID", "client-id")
os.environ.setdefault("SPOTIPY_CLIENT_SECRET", "client-secret")
os.environ.setdefault("SPOTIPY_REDIRECT_URI", "http://localhost/callback")
os.environ.setdefault("SPOTIFY_REFRESH_TOKEN", "refresh")
os.environ.setdefault("SETLIST_KEY", "setlist-key")
os.environ.setdefault("OPENAI_API_KEY", "sk-fake")
