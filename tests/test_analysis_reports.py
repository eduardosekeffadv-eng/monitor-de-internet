"""Análise de períodos, relatórios HTML e página de configurações."""

import asyncio
import json
import re
import time
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.api.server import create_app
from app.config import Settings
from app.events.detector import E_MICRO, E_OUTAGE, Event
from app.monitor.types import ProbeResult
from app.reports import builder
from app.services import analytics
from app.storage.db import Database
from conftest import FixedNet, ScriptedProber

R = {"cf": ProbeResult(30.0, 1), "gg": ProbeResult(40.0, 1)}


def _day_start(days_ago: int = 1) -> int:
    d = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    return int(d.timestamp() * 1000)


def _fill(db: Database, start: int, seconds: int, mode_id: int, sid: int, outage=None, lat=30.0):
    """Grava `seconds` amostras a partir de start; outage = (início_s, duração_s)."""
    for i in range(seconds):
        ts = start + i * 1000
        off = outage and outage[0] <= i < outage[0] + outage[1]
        db.insert_sample(ts, sid, mode_id, 4 if off else 0, "offline" if off else "online",
                         None if off else lat, R, 2 if off else 0, 2, None)
    db.commit()


def _scenario(tmp_path):
    db = Database(tmp_path / "a.db")
    s = Settings()
    s.provider_type = "fibra"
    day = _day_start(1)
    sid = db.start_session(day + 10 * 3600_000, 1000, "t", False, "fibra")
    m_cabo = db.open_mode_period("cabo", day + 10 * 3600_000, sid, True)
    t0 = day + 10 * 3600_000                         # ontem 10:00, 1 hora no cabo
    _fill(db, t0, 3600, m_cabo, sid, outage=(600, 60))
    db.insert_event(Event(type=E_OUTAGE, start_ts=t0 + 600_000, end_ts=t0 + 660_000, precision_ms=1000,
                          mode="cabo", mode_period_id=m_cabo, diagnosis="internet",
                          details={"diagnostico_titulo": "Provável falha na rede do provedor"}), sid)
    db.insert_event(Event(type=E_MICRO, start_ts=t0 + 1_800_000, end_ts=t0 + 1_802_000, precision_ms=1000,
                          mode="cabo", mode_period_id=m_cabo), sid)
    db.close_mode_period(m_cabo, t0 + 3600_000)
    m_wifi = db.open_mode_period("wifi_roteador", t0 + 3600_000, sid, True)
    _fill(db, t0 + 3600_000, 3600, m_wifi, sid, lat=60.0)   # 11:00–12:00 no Wi-Fi, sem quedas
    db.end_session(sid, t0 + 7200_000, "teste")
    db.aggregate_minutes(int(time.time() * 1000))
    return db, s, day, t0


def test_estatisticas_do_periodo(tmp_path):
    db, s, day, t0 = _scenario(tmp_path)
    st = analytics.period_stats(db, s, day, day + 86400_000)
    assert st["monitored_ms"] == 7200_000
    assert st["outage_count"] == 1 and st["outage_ms"] == 60_000
    assert abs(st["availability_pct"] - (100 * (7200 - 60) / 7200)) < 0.01
    assert st["micro_count"] == 1
    assert st["lat_avg"] == 45.1                      # média ponderada: 3540 × 30 ms e 3600 × 60 ms
    assert st["loss_pct"] == 0                        # ciclos em interrupção não contam como perda
    assert st["outage_longest"]["clipped_ms"] == 60_000
    assert st["rating"]["level"] in ("excelente", "boa", "regular", "ruim")


def test_comparativos_por_modo_dia_e_hora(tmp_path):
    db, s, day, t0 = _scenario(tmp_path)
    modes = {m["mode"]: m for m in analytics.by_mode(db, s, day, day + 86400_000)}
    assert modes["cabo"]["outages"] == 1 and modes["cabo"]["micro"] == 1
    assert modes["wifi_roteador"]["outages"] == 0 and modes["wifi_roteador"]["availability_pct"] == 100
    assert modes["cabo"]["lat_avg"] == 30.0 and modes["wifi_roteador"]["lat_avg"] == 60.0

    days = analytics.by_day(db, s, day, day + 86400_000)
    assert len(days) == 1 and days[0]["outages"] == 1 and days[0]["outage_ms"] == 60_000

    hm = analytics.heatmap(db, s, day, day + 86400_000)
    row = hm["days"][0]["hours"]
    assert row[10]["outages"] == 1 and row[10]["offline_ms"] == 60_000
    assert row[11]["offline_ms"] == 0 and row[9] is None
    assert hm["worst_hours"][0] == 10


def test_series_agregadas_por_tamanho_do_periodo(tmp_path):
    db, s, day, t0 = _scenario(tmp_path)
    raw = analytics.series(db, s, t0, t0 + 10 * 60_000)
    assert raw["kind"] == "raw" and len(raw["points"]) == 600
    ten = analytics.series(db, s, t0, t0 + 3600_000)
    assert ten["bucket_ms"] == 10_000 and len(ten["points"]) == 360
    day_s = analytics.series(db, s, day, day + 86400_000)
    assert day_s["bucket_ms"] == 5 * 60_000
    assert sum(p["n"] for p in day_s["points"]) == 7200
    assert any(p["n_offline"] for p in day_s["points"])


def test_periodos_dos_relatorios():
    s, e, label = builder.resolve_period("diario", "2026-10-16")
    assert e - s == 86400_000 and "16 de outubro de 2026" in label and label.startswith("Sexta-feira")
    s, e, label = builder.resolve_period("semanal", "2026-10-16")
    assert datetime.fromtimestamp(s / 1000).weekday() == 0 and e - s == 7 * 86400_000
    s, e, label = builder.resolve_period("mensal", "2026-02-10")
    assert datetime.fromtimestamp(s / 1000).day == 1 and label == "Fevereiro de 2026"
    assert datetime.fromtimestamp(e / 1000).month == 3
    for bad in [("personalizado", None, 10, 5), ("anual", None, None, None), ("diario", "16/10/2026", None, None)]:
        try:
            builder.resolve_period(*bad)
            raise AssertionError("deveria recusar")
        except builder.PeriodError:
            pass


def test_relatorio_html_autonomo(tmp_path):
    db, s, day, t0 = _scenario(tmp_path)
    s0, e0, label = builder.resolve_period("diario", datetime.fromtimestamp(day / 1000).strftime("%Y-%m-%d"))
    data = builder.report_data(db, s, {"type": "fibra", "label": "Fibra óptica", "name": "Teste</script>",
                                       "equip_name": "Modem/ONT de fibra", "equip_host": "192.168.100.1"},
                               "diario", s0, e0, label)
    assert data["stats"]["outage_count"] == 1 and len(data["outages"]) == 1
    assert "1 interrupção confirmada" in data["summary"]
    html = builder.build_html(data, "file")
    assert html.count("<script") == html.count("</script>") == 4        # nada fecha o <script> antes da hora
    assert "Teste<\\/script>" in html
    assert not re.search(r"<(script|link)[^>]+(src|href)=", html)          # nada é carregado de fora
    assert "{{" not in html.replace("{{ ", "")[:5000]
    assert "@font-face" in html and "base64," in html
    assert builder.filename("diario", s0, e0).startswith("relatorio-internet-diario-")


def test_api_analise_relatorios_e_configuracoes(tmp_path, store):
    store.update(interval_ms=500, timeout_ms=200, provider_type="starlink")
    db = Database(tmp_path / "api.db")
    app = create_app(store, db, ScriptedProber(), net_reader=FixedNet())
    with TestClient(app) as c:
        time.sleep(1.3)
        for page in ("/historico", "/relatorios", "/configuracoes"):
            r = c.get(page)
            assert r.status_code == 200 and "Monitor de Internet" in r.text
        a = c.get("/api/analysis", params={"start": int(time.time() * 1000) - 300_000}).json()
        assert a["series"]["kind"] == "raw" and a["stats"]["monitored_ms"] > 0
        assert c.get("/api/analysis", params={"start": 10, "end": 5}).status_code == 400
        assert "events" in c.get("/api/timeline").json()

        r = c.get("/relatorio", params={"tipo": "diario"})
        assert r.status_code == 200 and "REPORT_DATA" in r.text and 'data-mode="view"' in r.text
        r = c.get("/relatorio/baixar", params={"tipo": "semanal"})
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        assert 'data-mode="file"' in r.text
        assert c.get("/relatorio", params={"tipo": "personalizado"}).status_code == 400

        info = c.get("/api/settings").json()
        assert info["settings"]["interval_ms"] == 500 and info["autostart_supported"] in (True, False)
        res = c.post("/api/settings", json={"lat_normal_max_ms": 120, "interval_ms": 1000, "timeout_ms": 700,
                                            "loss_threshold_pct": 500, "hacker": 1}).json()
        st = res["settings"]
        assert st["lat_normal_max_ms"] == 120 and st["interval_ms"] == 1000
        assert st["loss_threshold_pct"] == 2.0 and res["warnings"]          # fora do limite: volta ao padrão
        assert "hacker" not in st
        # novo intervalo abre uma nova sessão (o tempo monitorado é calculado por sessão)
        sessions = db.conn.execute("SELECT interval_ms FROM sessions ORDER BY id").fetchall()
        assert [x[0] for x in sessions][-2:] == [500, 1000]
        res = c.post("/api/settings", json={"port": 8123}).json()
        assert res["restart_needed"] is True
        assert c.post("/api/settings", content="não é json", headers={"content-type": "application/json"}).status_code == 400
