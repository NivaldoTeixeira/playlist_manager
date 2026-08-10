"""Bot de Telegram que monta playlists no Spotify a partir de setlists reais.

Como o pacote está organizado, de fora para dentro:

    main.py               app FastAPI: rotas HTTP e ciclo de vida do bot
    telegram_handlers.py  transporte: traduz Telegram <-> pedido
    models.py             Show e Song, o vocabulário comum dos módulos
    config.py             leitura das variáveis de ambiente
    integrations/         tudo que fala com serviço externo

A dependência aponta sempre para dentro: `integrations` não conhece o Telegram,
e `models` não conhece ninguém.
"""
