"""Adaptadores dos serviços externos, um módulo por serviço.

    llm.py         interpreta o pedido em linguagem natural (OpenAI)
    setlist_fm.py  busca as setlists dos shows (API da setlist.fm)
    spotify.py     encontra as faixas e cria a playlist (API do Spotify)

Todos são síncronos de propósito — as bibliotecas usadas são. Quem chama é que
os joga em thread, para não travar o event loop (veja `service.py`).
"""
