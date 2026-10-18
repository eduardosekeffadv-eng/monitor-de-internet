"""API web, segurança local, leitura do Wi-Fi e estratégia ICMP/TCP."""

import asyncio
import time

from fastapi.testclient import TestClient

from app.api.server import create_app
from app.monitor.probers import RealProber
from app.monitor.types import M_FAIL, M_ICMP, M_TCP, ProbeResult
from app.monitor.wifi import parse_netsh
from app.storage.db import Database
from conftest import FixedNet, ScriptedProber

NETSH_PT = """
Há 1 interface no sistema:

    Nome                   : Wi-Fi
    Descrição              : Intel(R) Wi-Fi 6 AX201 160MHz
    Estado                 : conectado
    SSID                   : CasaStarlink
    Banda                  : 5 GHz
    Sinal                  : 34%
    Rssi                   : -71
"""

NETSH_EN_DISCONNECTED = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    State                  : disconnected
"""

NETSH_LOCATION = """
Network shell commands need location permission to access WLAN information. Turn on Location services on the Location page in Privacy & security settings.
"""


def test_parse_netsh_portugues():
    w = parse_netsh(NETSH_PT)
    assert w.available and w.connected
    assert w.signal_pct == 34 and w.rssi_dbm == -71 and w.ssid == "CasaStarlink"


def test_parse_netsh_desconectado_e_localizacao():
    w = parse_netsh(NETSH_EN_DISCONNECTED)
    assert w.available and not w.connected and w.signal_pct is None
    loc = parse_netsh(NETSH_LOCATION)
    assert not loc.available and loc.error == "localizacao"


def test_icmp_bloqueado_usa_tcp():
    calls = {"tcp": 0}

    def icmp(host, timeout):
        return ProbeResult(None, M_FAIL, "tempo esgotado")

    async def tcp(host, port, timeout):
        calls["tcp"] += 1
        return ProbeResult(25.0, M_TCP)

    async def scenario():
        p = RealProber(icmp_ping=icmp, tcp=tcp)
        r1 = await p.probe("cf", "1.1.1.1", 443, 800)
        assert not r1.ok and calls["tcp"] == 0          # 1º ciclo: só ICMP
        r2 = await p.probe("cf", "1.1.1.1", 443, 800)
        assert r2.ok and r2.method == M_TCP              # suspeito: ICMP + TCP em paralelo
        await p.probe("cf", "1.1.1.1", 443, 800)
        assert "TCP" in p.method_info("cf")              # passa a usar TCP como principal
        p.close()

    asyncio.run(scenario())


def test_icmp_ok_nao_usa_tcp():
    async def tcp(host, port, timeout):
        raise AssertionError("TCP não deveria ser usado")

    async def scenario():
        p = RealProber(icmp_ping=lambda h, t: ProbeResult(12.0, M_ICMP), tcp=tcp)
        for _ in range(5):
            assert (await p.probe("gg", "8.8.8.8", 443, 800)).ok
        p.close()

    asyncio.run(scenario())


def test_api_estado_modo_e_seguranca(tmp_path, store):
    store.update(interval_ms=500, timeout_ms=200)
    db = Database(tmp_path / "api.db")
    app = create_app(store, db, ScriptedProber(), net_reader=FixedNet())
    with TestClient(app) as client:
        time.sleep(1.4)
        h = client.get("/api/health").json()
        assert h["app"] == "monitor-de-internet" and h["running"]

        st = client.get("/api/state").json()
        assert st["conn_status"] == "online"
        assert len(st["samples"]) >= 2
        assert st["mode"]["confirmed"] is False
        assert {t["id"] for t in st["targets"]} == {"gw", "cf", "gg", "dish"}

        r = client.post("/api/mode", json={"mode": "wifi_direto"})
        assert r.status_code == 200 and r.json()["mode"] == "wifi_direto"
        assert client.post("/api/mode", json={"mode": "satelite"}).status_code == 400

        # outros sites abertos no navegador não podem acionar o sistema
        assert client.post("/api/mode", json={"mode": "cabo"},
                           headers={"Origin": "https://site-qualquer.com"}).status_code == 403
        assert client.get("/api/health", headers={"Host": "exemplo.com"}).status_code == 403

        # tipo de conexão pela API
        st2 = client.get("/api/state").json()
        assert st2["provider"]["configured"] is False and len(st2["provider"]["profiles"]) == 7
        r = client.post("/api/provider", json={"type": "cabo", "name": "Claro"})
        assert r.status_code == 200 and r.json()["equip_name"] == "Modem a cabo"
        assert client.post("/api/provider", json={"type": "cabo", "equip_host": "abc"}).status_code == 400
        assert client.post("/api/provider", json={"type": "xyz"}).status_code == 400

        page = client.get("/")
        assert page.status_code == 200 and "Monitor de Internet" in page.text
        assert client.get("/static/vendor/chart.umd.min.js").status_code == 200

        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] in ("sample", "stats", "event", "mode")
