"""End-to-end fixtures: a real server process, real client forwarder, real SQLite file."""
from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = ROOT / "server"
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "tools"))

SITES = ("home", "cabin", "shed")


@pytest.fixture(autouse=True)
def _allow_local_sockets(socket_enabled):
    yield


@pytest.fixture
def expected_lingering_timers() -> bool:
    return True


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerProcess:
    """Runs uvicorn exactly as the container does: factory plus a config directory.

    The config directory has empty secrets, so every start exercises the server's own key generation.
    """

    def __init__(self, tmp_path: Path, flush_interval: float = 0.5) -> None:
        self.config_dir = tmp_path / "config"
        shutil.copytree(SERVER_DIR / "config.example", self.config_dir)
        self.db_path = tmp_path / "e2e.db"
        (self.config_dir / "config.yaml").write_text(yaml.safe_dump({
            "cookie_secure": False, "db_path": str(self.db_path), "flush_interval": flush_interval}))
        (self.config_dir / "topology.yaml").write_text(yaml.safe_dump({
            "sites": [{"name": s.title(), "key": f"key-{s}"} for s in SITES]}))
        (self.config_dir / "auth.yaml").write_text(
            'ui_key: ""\nsession_secret: ""\nkeys:\n' + "".join(f'  key-{s}: ""\n' for s in SITES))
        self.port = free_port()
        self.proc: subprocess.Popen | None = None
        self.log = tmp_path / "server.log"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ingest_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws/ingest"

    def secrets(self) -> dict:
        return yaml.safe_load((self.config_dir / "auth.yaml").read_text())

    @property
    def ui_key(self) -> str:
        return self.secrets()["ui_key"]

    @property
    def keys(self) -> dict[str, str]:
        """site id -> site key, as generated into auth.yaml."""
        return {s: self.secrets()["keys"][f"key-{s}"] for s in SITES}

    def start(self) -> None:
        env = {**os.environ, "ENERGY_HUB_CONFIG_DIR": str(self.config_dir), "LOG_LEVEL": "INFO"}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:create_app_from_env", "--factory",
             "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=SERVER_DIR, env=env, stdout=self.log.open("ab"), stderr=subprocess.STDOUT)
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return
            except OSError:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"server exited early:\n{self.log.read_text()}")
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    def terminate(self) -> int:
        """SIGTERM like `docker stop`; returns the exit code."""
        assert self.proc is not None
        self.proc.send_signal(signal.SIGTERM)
        try:
            return self.proc.wait(timeout=15)
        finally:
            self.proc = None

    def cpu_seconds(self) -> float:
        assert self.proc is not None
        fields = Path(f"/proc/{self.proc.pid}/stat").read_text().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")

    def kill(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


@pytest.fixture
def server(tmp_path):
    s = ServerProcess(tmp_path)
    s.start()
    yield s
    s.kill()


@pytest.fixture
async def api(server):
    """Logged-in aiohttp session for the server's REST API."""
    async with aiohttp.ClientSession(
        server.base_url, cookie_jar=aiohttp.CookieJar(unsafe=True)  # unsafe: the host is an IP address
    ) as session:
        r = await session.post("/api/login", json={"key": server.ui_key})
        assert r.status == 200
        yield session
