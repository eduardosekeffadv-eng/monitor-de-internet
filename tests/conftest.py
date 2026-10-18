"""Utilitários dos testes: ciclos e sondas simuladas de forma determinística."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings, SettingsStore  # noqa: E402
from app.monitor.types import M_FAIL, M_ICMP, Cycle, NetInfo, ProbeResult, WifiInfo  # noqa: E402

FAIL = "FAIL"
T0 = 1_800_000_000_000   # horário base (ms)


def pr(v) -> ProbeResult:
    if v is None:
        return ProbeResult.not_probed()
    if v == FAIL:
        return ProbeResult(None, M_FAIL, "tempo esgotado")
    return ProbeResult(float(v), M_ICMP)


def cycle(i: int, cf=30, gg=32, gw=1, dish=None, mode="cabo", gateway="192.168.1.1",
          wifi_signal=None, wifi_connected=True, interval=1000) -> Cycle:
    wifi = None
    if wifi_signal is not None or not wifi_connected:
        wifi = WifiInfo(available=True, connected=wifi_connected,
                        signal_pct=wifi_signal if wifi_connected else None)
    return Cycle(ts=T0 + i * interval,
                 results={"gw": pr(gw), "cf": pr(cf), "gg": pr(gg), "dish": pr(dish)},
                 net=NetInfo(gateway=gateway, if_index=5, if_type="cabo", if_name="Ethernet"),
                 wifi=wifi, mode=mode, mode_period_id=1)


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def store(tmp_path) -> SettingsStore:
    return SettingsStore(tmp_path / "config.json")


class ScriptedProber:
    """Sonda controlada pelo teste: define-se a resposta de cada destino."""

    simulated = False

    def __init__(self) -> None:
        self.plan = {"gw": 1.0, "cf": 30.0, "gg": 32.0, "dish": FAIL}

    def set(self, **values) -> None:
        self.plan.update(values)

    def method_info(self, tid: str) -> str:
        return "ICMP"

    async def probe(self, tid, host, tcp_port, timeout_ms):
        return pr(self.plan.get(tid, FAIL))

    def close(self) -> None:
        pass


class FixedNet:
    def __init__(self, gateway="192.168.1.1", if_type="cabo"):
        self.info = NetInfo(gateway=gateway, if_index=3, if_type=if_type, if_name="Ethernet de teste")

    def read(self) -> NetInfo:
        return self.info
