"""Authentication helpers: site tokens (ingest) and shared UI key (browser)."""
from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque

from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import Config, SiteDef

COOKIE_NAME = "eh_session"
_SALT = "energy-hub-session"


def site_for_authorization(cfg: Config, header: str | None) -> SiteDef | None:
    """Resolve `Authorization: Bearer <token>` to a site using constant-time compares."""
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    found: SiteDef | None = None
    for site in cfg.sites:  # no early exit: keep timing independent of the match position
        if hmac.compare_digest(site.token.encode(), token.strip().encode()):
            found = site
    return found


def check_ui_key(cfg: Config, key: str) -> bool:
    return hmac.compare_digest(cfg.ui_key.encode(), key.encode())


def _serializer(cfg: Config) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(cfg.session_secret, salt=_SALT)


def issue_session(cfg: Config) -> str:
    return _serializer(cfg).dumps({"v": 1})


def session_valid(cfg: Config, token: str | None) -> bool:
    if not token:
        return False
    try:
        data = _serializer(cfg).loads(token, max_age=cfg.session_days * 86400)
    except BadSignature:
        return False
    return isinstance(data, dict) and data.get("v") == 1


class LoginLimiter:
    """Allow `limit` failed logins per `window` seconds per client address."""

    def __init__(self, limit: int = 5, window: float = 60.0, clock=time.monotonic) -> None:
        self.limit, self.window, self.clock = limit, window, clock
        self._fails: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, who: str) -> deque[float]:
        q = self._fails[who]
        cutoff = self.clock() - self.window
        while q and q[0] < cutoff:
            q.popleft()
        if not q:
            self._fails.pop(who, None)
            return deque()
        return q

    def blocked(self, who: str) -> bool:
        return len(self._prune(who)) >= self.limit

    def record_failure(self, who: str) -> None:
        self._prune(who)
        self._fails[who].append(self.clock())

    def reset(self, who: str) -> None:
        self._fails.pop(who, None)
