from __future__ import annotations

from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def build_session(http_config: Any) -> requests.Session:
    """Build a `requests.Session` with retry policy wired from config.

    The retry adapter is mounted on `https://` and configured to retry only
    on the status codes listed in `http_config.retry_status` (e.g. 502/503/524).
    Auth (4xx) responses bypass the retry policy and surface immediately.

    Args:
        http_config: Object with `retries`, `backoff_factor`, and
            `retry_status` attributes (e.g. `FetchConfig.http`).

    Returns:
        A configured `requests.Session`.
    """
    retry = Retry(
        total=http_config.retries,
        backoff_factor=http_config.backoff_factor,
        status_forcelist=list(http_config.retry_status),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
