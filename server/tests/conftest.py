from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Config, SiteDef
from app.devices import load_catalogue
from app.main import create_app

HOME_TOKEN = "home-token-0123456789"
CABIN_TOKEN = "cabin-token-0123456789"
UI_KEY = "ui-key-12345"

EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "config.example"


def make_config(db_path: str, **over) -> Config:
    """A ready-made Config using the shipped device catalogue and two sites."""
    values = {
        "ui_key": UI_KEY,
        "session_secret": "s" * 32,
        "cookie_secure": False,
        "db_path": db_path,
        "flush_interval": 0.1,
        "sites": [
            SiteDef(id="home", name="Home", token=HOME_TOKEN),
            SiteDef(id="cabin", name="Cabin", token=CABIN_TOKEN),
        ],
        "catalogue": load_catalogue(EXAMPLE_DIR / "devices.yaml"),
    }
    values.update(over)
    return Config(**values)


@pytest.fixture
def config(tmp_path):
    return make_config(str(tmp_path / "test.db"))


@pytest.fixture
def client(config):
    app = create_app(config, retention_interval=3600)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def authed(client):
    r = client.post("/api/login", json={"key": UI_KEY})
    assert r.status_code == 200
    return client
