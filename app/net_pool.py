# net_pool.py
"""
Одна общая HTTP-сессия на весь плеер.

requests.get()/post() по умолчанию на КАЖДЫЙ запрос создают новую сессию: заново грузят хранилище
сертификатов (load_verify_locations) и делают TLS-рукопожатие. Обложки, исполнители, тренды «Для вас»,
жанры — это десятки запросов подряд в фоновых потоках, и каждое рукопожатие держит GIL: интерфейс
подтормаживает, а процессор уходит в 100 %+. Здесь requests.request подменяется на запрос через одну
сессию с пулом соединений: сертификаты грузятся один раз, соединения к тем же сайтам переиспользуются.
Вызывается один раз при старте (main.py). Поведение запросов не меняется.
"""
from __future__ import annotations

import threading

_installed = False
_lock = threading.Lock()


def install():
    global _installed
    with _lock:
        if _installed:
            return
        _installed = True
    try:
        import requests
        import requests.api
        from requests.adapters import HTTPAdapter
    except Exception:                                          # noqa: BLE001
        return
    s = requests.Session()
    ad = HTTPAdapter(pool_connections=24, pool_maxsize=24, max_retries=0)
    s.mount("https://", ad)
    s.mount("http://", ad)

    def request(method, url, **kwargs):
        return s.request(method=method, url=url, **kwargs)

    requests.api.request = request                             # get/post/head/… в requests.api зовут request
    for name in ("get", "post", "head", "put", "delete", "patch", "options"):
        fn = getattr(requests.api, name)
        setattr(requests, name, fn)
