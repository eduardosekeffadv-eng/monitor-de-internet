"""Banco de dados, motor de monitoramento, recuperação após falhas e estatísticas."""

import asyncio
import json

from app.events.detector import E_OUTAGE, Event
from app.monitor.engine import MonitorEngine
from app.services.stats import compute_stats
from app.storage.db import Database
from conftest import FAIL, FixedNet, ScriptedProber, T0


def make_engine(tmp_path, store, prober=None, net=None):
    db = Database(tmp_path / "monitor.db")
    msgs = []
    eng = MonitorEngine(store, db, prober or ScriptedProber(), msgs.append, net_reader=net or FixedNet())
    return eng, db, msgs


def test_recuperacao_apos_encerramento_inesperado(tmp_path):
    db = Database(tmp_path / "m.db")
    sid = db.start_session(T0, 1000, "teste", False)
    ev = Event(type=E_OUTAGE, start_ts=T0 + 5000, precision_ms=1000, mode="cabo")
    db.insert_event(ev, sid)
    db.commit()
    db.conn.close()                     # "queda" do processo: nada foi encerrado

    db2 = Database(tmp_path / "m.db")
    info = db2.recover_previous()
    assert info["crashed"] and info["closed_events"] == 1
    row = db2.list_events(types=[E_OUTAGE])[0]
    assert not row["open"] and row["truncated"]
    assert "parou de medir" in row["details"]["observacao"]
    s = db2.conn.execute("SELECT end_reason FROM sessions").fetchone()[0]
    assert s == "encerramento inesperado"


def test_motor_registra_interrupcao_e_estatisticas(tmp_path, store):
    async def scenario():
        prober = ScriptedProber()
        eng, db, msgs = make_engine(tmp_path, store, prober)
        await eng.start(run_loops=False)
        base = eng.session_start + 1000
        for i in range(20):
            await eng.run_cycle(base + i * 1000)
        prober.set(cf=FAIL, gg=FAIL)
        for i in range(20, 30):                  # 10 s sem internet, roteador ok
            await eng.run_cycle(base + i * 1000)
        assert eng.conn_status == "offline"
        prober.set(cf=31.0, gg=33.0)
        for i in range(30, 40):
            await eng.run_cycle(base + i * 1000)
        assert eng.conn_status in ("online", "instavel")

        outs = db.list_events(types=["interrupcao"])
        assert len(outs) == 1
        o = outs[0]
        assert o["duration_ms"] == 10_000 and o["precision_ms"] == 1000
        assert o["diagnosis"] == "internet" and o["router_state"] == "respondendo normalmente"
        assert o["mode"] == "cabo" and o["lat_after"] is not None

        # mensagens em tempo real chegaram ao painel
        types = {m["type"] for m in msgs}
        assert {"sample", "event"} <= types
        assert any(m["type"] == "sample" and m["sample"]["status"] == 4 for m in msgs)

        st = compute_stats(db, base + 40_000, eng.session_start)
        if st["period_start"] > base:      # teste rodando perto da meia-noite: o "hoje" virou
            return db
        assert st["outages_today"] == 1
        assert st["outage_ms_today"] == 10_000
        assert st["monitored_ms_today"] == 40_000
        assert abs(st["availability_pct"] - 75.0) < 0.01
        assert st["online_since_kind"] == "ultima_interrupcao"

        n = db.conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert n == 40
        await eng.stop("teste")
        return db

    asyncio.run(scenario())
    # reabrir: nada fica aberto, sessão encerrada corretamente
    db2 = Database(tmp_path / "monitor.db")
    info = db2.recover_previous()
    assert not info["crashed"]
    assert all(not e["open"] for e in db2.list_events())


def test_suspensao_vira_monitor_inativo_e_nao_queda(tmp_path, store):
    async def scenario():
        prober = ScriptedProber()
        eng, db, _ = make_engine(tmp_path, store, prober)
        await eng.start(run_loops=False)
        base = eng.session_start + 1000
        for i in range(5):
            await eng.run_cycle(base + i * 1000)
        prober.set(cf=FAIL, gg=FAIL)
        for i in range(5, 9):
            await eng.run_cycle(base + i * 1000)
        # computador suspenso por 1 hora
        await eng._handle_gap(base + 8000, base + 3_608_000, "computador suspenso")
        prober.set(cf=30.0, gg=30.0)
        await eng.run_cycle(base + 3_608_000)

        inactive = db.list_events(types=["monitor_inativo"])
        assert any(e["duration_ms"] == 3_600_000 for e in inactive)
        out = db.list_events(types=["interrupcao"])[0]
        assert out["truncated"] and out["end_ts"] == base + 8000   # não se estende para a suspensão
        # a hora suspensa não conta como tempo monitorado (independe da hora do dia)
        assert db.monitored_ms(base, base + 3_609_001) == 10_000
        await eng.stop("teste")

    asyncio.run(scenario())


def test_troca_de_modo(tmp_path, store):
    async def scenario():
        eng, db, msgs = make_engine(tmp_path, store)
        await eng.start(run_loops=False)
        first_period = eng.mode_period_id
        # logo após iniciar: corrige o período atual (retroativo)
        info = await eng.set_mode("wifi_roteador")
        assert info["confirmed"] and eng.mode_period_id == first_period
        assert db.get_mode_period(first_period)["mode"] == "wifi_roteador"
        assert store.settings.last_mode == "wifi_roteador"
        # já confirmado: troca abre um novo período
        await eng.set_mode("cabo")
        assert eng.mode_period_id != first_period
        assert db.get_mode_period(first_period)["ended_at"] is not None
        assert any(e["type"] == "modo_alterado" for e in db.list_events())
        await eng.stop("teste")

    asyncio.run(scenario())


def test_agregacao_e_retencao(tmp_path):
    db = Database(tmp_path / "a.db")
    sid = db.start_session(T0, 1000, "t", False)
    from app.monitor.types import ProbeResult
    r = {"cf": ProbeResult(30.0, 1), "gg": ProbeResult(40.0, 1)}
    for i in range(180):
        db.insert_sample(T0 + i * 1000, sid, 1, 0, "online", 30.0 + (i % 3), r, 0, 2, None)
    db.commit()
    db.aggregate_minutes(T0 + 180_000)
    rows = db.conn.execute("SELECT * FROM agg_minute ORDER BY minute_ts").fetchall()
    assert len(rows) >= 2 and all(r["n"] > 0 for r in rows)
    deleted = db.apply_retention(T0 + 60_000, T0 - 1)
    assert deleted >= 1
    assert db.conn.execute("SELECT MIN(ts) FROM samples").fetchone()[0] >= T0 + 60_000
    # agregados preservados após apagar os brutos
    assert db.conn.execute("SELECT COUNT(*) FROM agg_minute").fetchone()[0] == len(rows)


def test_config_invalida_usa_padroes(tmp_path):
    from app.config import SettingsStore
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"interval_ms": "abc", "timeout_ms": 5000, "lat_normal_max_ms": 80,
                             "targets": [{"id": "cf", "enabled": False}, {"id": "gg", "enabled": False}]}))
    s = SettingsStore(p).settings
    assert s.interval_ms == 1000
    assert s.timeout_ms < s.interval_ms
    assert s.lat_normal_max_ms == 80
    assert s.target("cf").enabled and s.target("gg").enabled    # pelo menos um externo sempre ativo


def test_grafico_recupera_historico_recente_apos_reiniciar(tmp_path, store):
    async def scenario():
        eng, db, _ = make_engine(tmp_path, store)
        await eng.start(run_loops=False)
        base = eng.session_start - 60_000
        for i in range(30):
            await eng.run_cycle(base + i * 1000)
        await eng.stop("teste")
        db.close()

        eng2, db2, _ = make_engine(tmp_path, store)
        await eng2.start(run_loops=False)
        snap = eng2.snapshot()
        assert len(snap["samples"]) == 30          # histórico vem do banco
        await eng2.run_cycle(eng2.session_start + 1000)
        snap = eng2.snapshot()
        assert len(snap["samples"]) == 31
        ts = [x["ts"] for x in snap["samples"]]
        assert ts == sorted(ts)
        await eng2.stop("teste")

    asyncio.run(scenario())


def test_linhas_por_minuto_incluem_minutos_recentes(tmp_path):
    from app.monitor.types import ProbeResult
    db = Database(tmp_path / "r.db")
    sid = db.start_session(T0, 1000, "t", False)
    r = {"cf": ProbeResult(30.0, 1), "gg": ProbeResult(40.0, 1)}
    for i in range(150):
        db.insert_sample(T0 + i * 1000, sid, 1, 0, "online", 30.0, r, 0, 2, None)
    db.commit()
    db.aggregate_minutes(T0 + 60_000)      # parte já agregada; o restante é lido na hora
    rows = db.minute_rows(T0, T0 + 150_000)
    assert sum(r["n"] for r in rows) == 150
    assert all(r["covered_ms"] for r in rows)
