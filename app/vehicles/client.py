"""A polite HTTP client: browser User-Agent, timeout, and a pause between requests to a site."""

import time
from urllib.parse import urlsplit

import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class Client:
    def __init__(self, delay: float = 1.0, timeout: float = 30.0):
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": USER_AGENT, "Accept-Language": "es-CO,es;q=0.9,en;q=0.8"}
        )
        self._last_request: dict[str, float] = {}

    def get(self, url: str, **kwargs) -> requests.Response:
        """GET the URL, waiting so that `delay` seconds pass after the last request to its host."""
        host = urlsplit(url).netloc
        last = self._last_request.get(host)
        if last is not None:
            wait = last + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        try:
            response = self.session.get(url, timeout=self.timeout, **kwargs)
        finally:
            self._last_request[host] = time.monotonic()
        response.raise_for_status()
        return response
