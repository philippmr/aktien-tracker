"""HTTP-Helfer mit Rate-Limit, Retries und Datei-Cache."""
import hashlib
import os
import threading
import time

import requests

import config

_lock = threading.Lock()
_last_call: dict[str, float] = {}
# SEC erlaubt max. 10 Requests/Sekunde. Wir bleiben darunter.
_MIN_INTERVAL = {"sec.gov": 0.15, "house.gov": 0.2, "senate.gov": 0.5}

_session = requests.Session()
_session.headers["User-Agent"] = config.USER_AGENT
_session.headers["Accept-Encoding"] = "gzip, deflate"


def _throttle(url: str) -> None:
    host = next((h for h in _MIN_INTERVAL if h in url), None)
    if not host:
        return
    with _lock:
        wait = _last_call.get(host, 0) + _MIN_INTERVAL[host] - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.time()


def cache_path(url: str, subdir: str, suffix: str = "") -> str:
    d = os.path.join(config.CACHE_DIR, subdir)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, hashlib.sha1(url.encode()).hexdigest()[:20] + suffix)


def get(url: str, *, cache: str | None = None, max_age_h: float | None = None,
        suffix: str = "", retries: int = 6) -> bytes | None:
    """Lädt eine URL. Mit `cache` wird das Ergebnis auf Platte gespeichert.
    max_age_h=None bedeutet: Cache gilt für immer (für unveränderliche Dokumente)."""
    path = cache_path(url, cache, suffix) if cache else None
    if path and os.path.exists(path):
        age_h = (time.time() - os.path.getmtime(path)) / 3600
        if max_age_h is None or age_h < max_age_h:
            with open(path, "rb") as f:
                return f.read()

    for attempt in range(retries):
        _throttle(url)
        try:
            r = _session.get(url, timeout=30)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            if path:
                with open(path, "wb") as f:
                    f.write(r.content)
            return r.content
        if r.status_code == 404:
            return None
        if r.status_code in (403, 408, 429, 500, 502, 503, 504):
            time.sleep(min(2 ** attempt * 2, 60))
            continue
        return None
    return None
