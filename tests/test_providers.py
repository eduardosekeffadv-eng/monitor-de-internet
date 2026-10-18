"""Tipo de conexão: migração da versão 1.x, troca de tipo e diagnóstico por tipo."""

import asyncio
import json
import sqlite3

from app.config import SettingsStore
from app.events.detector import E_OUTAGE, Detector
from app.monitor.engine import MonitorEngine
from app.storage.db import Database
from conftest import FAIL, FixedNet, ScriptedProber, cycle


def test_config_da_versao_1_vira_starlink(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"last_mode": "wifi_roteador",
                             "targets": [{"id": "dish", "name": "Antena Starlink", "host": "192.168.100.1",
                                          "tcp_port": 9200, "enabled": True}]}), encoding="utf-8")
    s = SettingsStore(p).settings
    assert s.provider_type == "starlink"
    assert s.target("dish").name == "Antena Starlink"
    assert json.loads(p.read_text(encoding="utf-8"))["provider_type"] == "starlink"   # gravado


def test_instalacao_nova_pergunta_o_tipo(tmp_path):
    s = SettingsStore(tmp_path / "config.json").settings
    assert s.provider_type == ""


def test_banco_da_versao_1_e_migrado(tmp_path):
    path = tmp_path / "v1.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at INTEGER NOT NULL,"
              " ended_at INTEGER, end_reason TEXT, interval_ms INTEGER NOT NULL, version TEXT,"
              " simulated INTEGER NOT NULL DEFAULT 0)")
    c.execute("INSERT INTO sessions(started_at, interval_ms, version) VALUES (1, 1000, '1.0.1')")
    c.commit(); c.close()
    db = Database(path)
    row = db.conn.execute("SELECT provider FROM sessions").fetchone()
    assert row[0] == "starlink"
    assert db.get_meta("schema_version") == "4"


def _outage(det, **kw):
    seq = [cycle(i, **kw) for i in range(3)]
    seq += [cycle(i, cf=FAIL, gg=FAIL, **kw) for i in range(3, 8)]
    seq += [cycle(8, **kw)]
    outs = [det.process(c) for c in seq]
    return [e for a, e in outs[8].changes if e.type == E_OUTAGE][0]


def test_diagnostico_usa_o_tipo_de_conexao(settings):
    settings.provider_type = "fibra"
    ev = _outage(Detector(settings), dish=1)
    assert ev.diagnosis == "internet_antena_ok"
    assert "modem/ONT" in ev.diagnosis_text and "satélite" not in ev.diagnosis_text
    assert ev.details["diagnostico_titulo"] == "Provável falha na rede do provedor"

    settings.provider_type = "starlink"
    ev = _outage(Detector(settings), dish=1)
    assert "antena Starlink" in ev.diagnosis_text
    assert "satélite" in ev.details["diagnostico_titulo"]

    settings.provider_type = "fibra"
    ev = _outage(Detector(settings), dish=FAIL)
    assert ev.details["diagnostico_titulo"] == "Provável falha entre o roteador e o modem/ONT de fibra"


def test_troca_de_tipo_de_conexao(tmp_path, store):
    async def scenario():
        db = Database(tmp_path / "m.db")
        msgs = []
        eng = MonitorEngine(store, db, ScriptedProber(), msgs.append, net_reader=FixedNet())
        await eng.start(run_loops=False)
        assert eng.provider_info()["configured"] is False

        info = await eng.set_provider("fibra", "Vivo Fibra")
        assert info["type"] == "fibra" and info["name"] == "Vivo Fibra"
        dish = store.settings.target("dish")
        assert dish.name == "Modem/ONT de fibra" and dish.host == "192.168.100.1" and dish.enabled
        assert store.settings.lat_normal_max_ms == 60     # limites recomendados aplicados

        # 4G sem endereço: equipamento fica sem monitorar
        await eng.set_provider("movel", "", None, apply_thresholds=False)
        assert not store.settings.target("dish").enabled
        assert store.settings.lat_normal_max_ms == 60     # limites mantidos

        # endereço personalizado
        await eng.set_provider("radio", "", "10.0.0.2")
        assert store.settings.target("dish").host == "10.0.0.2"
        try:
            await eng.set_provider("radio", "", "999.1.1")
            raise AssertionError("deveria recusar")
        except ValueError:
            pass

        evs = db.list_events(types=["tipo_conexao_alterado"])
        assert len(evs) == 3 and evs[-1]["details"]["para"] == "Fibra óptica"
        assert any(m["type"] == "provider" for m in msgs)
        assert db.conn.execute("SELECT provider FROM sessions").fetchone()[0] == "radio"
        await eng.stop("teste")

    asyncio.run(scenario())
