"""Dados da antena Starlink: motivos das quedas, correlação, alertas e falhas da API."""

import asyncio
import time

from fastapi.testclient import TestClient

from app.api.server import create_app
from app.config import SettingsStore
from app.monitor.dish_api import (GPS_EPOCH_MS, DishUnavailable, cause_info, gps_ns_to_ms, state_label)
from app.monitor.engine import MonitorEngine
from app.reports import builder
from app.services import analytics
from app.storage.db import Database
from conftest import FAIL, FixedNet, ScriptedProber


class FakeDish:
    """Antena de teste: o teste define o que ela responde."""

    simulated = False

    def __init__(self, host="192.168.100.1"):
        self.host = host
        self.alerts: list[str] = []
        self.outages: list[dict] = []
        self.down = False
        self.lib = True
        self.counter = 1000

    def library_available(self):
        return self.lib

    def close(self):
        pass

    def status(self):
        if self.down:
            raise DishUnavailable("tempo esgotado")
        return {"state": "CONNECTED", "uptime_s": 3600, "hardware": "mini1", "software": "x", "id_masked": "",
                "pop_latency_ms": 22.0, "drop_rate": 0.0, "down_bps": 1e5, "up_bps": 1e4,
                "obstructed_now": None, "fraction_obstructed": None, "alerts": list(self.alerts),
                "alerts_known": ["thermal_throttle", "is_heating"]}

    def history(self, start_counter):
        if self.down:
            raise DishUnavailable("tempo esgotado")
        prev, self.counter = self.counter, self.counter + 60
        window = None if start_counter is None else {
            "samples": 60, "drop_s": 1.5, "full_drop_s": 1, "obstructed_s": 0.5, "full_obstructed_s": 0,
            "unscheduled_s": 0.0, "full_unscheduled_s": 0, "lat_mean_ms": 24.0, "power_w": 18.0,
            "down_bytes": 1000, "up_bytes": 100}
        return {"outages": list(self.outages), "end_counter": self.counter, "window": window}

    def obstruction_map(self):
        return {"rows": 2, "cols": 2, "data": [[1.0, -1.0], [0.0, 1.0]]}


def starlink_store(store):
    store.update(provider_type="starlink")
    return store


def make(tmp_path, store, dish):
    db = Database(tmp_path / "monitor.db")
    msgs = []
    eng = MonitorEngine(starlink_store(store), db, ScriptedProber(), msgs.append, net_reader=FixedNet(),
                        dish_source_factory=lambda host: dish)
    return eng, db, msgs


def test_motivos_e_horario_gps():
    assert gps_ns_to_ms(0) == GPS_EPOCH_MS - 18_000
    assert cause_info("NO_PINGS", True)["label"] == "Troca de satélite"
    assert cause_info("NO_PINGS", True)["side"] == "starlink"
    assert cause_info("OBSTRUCTED")["side"] == "local"
    assert cause_info("ALGO_NOVO")["cause"] == "UNKNOWN"
    assert "ALGO_NOVO" in cause_info("ALGO_NOVO")["label"]
    assert state_label("CONNECTED") == "Conectada"


def test_queda_do_monitor_recebe_motivo_da_antena(tmp_path, store):
    async def scenario():
        dish = FakeDish()
        eng, db, msgs = make(tmp_path, store, dish)
        await eng.start(run_loops=False)
        base = eng.session_start + 1000
        prober = eng.prober
        for i in range(10):
            await eng.run_cycle(base + i * 1000)
        prober.set(cf=FAIL, gg=FAIL)
        await eng.run_cycle(base + 10_000)            # 1 ciclo sem resposta = microqueda
        prober.set(cf=30.0, gg=31.0)
        for i in range(11, 15):
            await eng.run_cycle(base + i * 1000)
        micro = db.list_events(types=["microqueda"])
        assert len(micro) == 1 and "dish" not in micro[0]

        # a antena informa (com o relógio dela ~1 s diferente) uma queda por troca de satélite
        dish.outages = [{"start_ts": base + 9_200, "duration_ms": 1_400, "cause": "NO_PINGS", "did_switch": True},
                        # outra queda curta que o monitor não percebeu
                        {"start_ts": base + 60_000, "duration_ms": 300, "cause": "OBSTRUCTED",
                         "did_switch": False}]
        await eng.dish_api.step(force=True)            # 1ª consulta: só marca o contador
        assert eng.dish_api.info.status == "ativa"
        micro = db.list_events(types=["microqueda"])[0]
        assert micro["dish"]["label"] == "Troca de satélite" and micro["dish"]["side"] == "starlink"
        assert any(m["type"] == "event" and m["event"].get("dish") for m in msgs)

        # repetir a consulta não duplica as quedas
        await eng.dish_api.step(force=True)
        assert db.conn.execute("SELECT COUNT(*) FROM dish_outages").fetchone()[0] == 2
        assert db.conn.execute("SELECT COUNT(*) FROM dish_stats").fetchone()[0] == 1

        now = base + 120_000
        da = analytics.dish_analysis(db, base - 1000, now, now)
        assert da["summary"]["count"] == 2 and da["matched"] == 1 and da["only_dish"] == 1
        assert da["stats"]["power_w"] == 18.0
        assert "antena Starlink" in analytics.dish_sentence(da)
        pub = eng.dish_api.public()
        assert pub["connected"] and pub["power_w"] == 18.0

    asyncio.run(scenario())


def test_alertas_da_antena_viram_eventos(tmp_path, store):
    async def scenario():
        dish = FakeDish()
        eng, db, msgs = make(tmp_path, store, dish)
        await eng.start(run_loops=False)
        await eng.dish_api.step(force=True)
        assert not db.list_events(types=["alerta_antena"])
        dish.alerts = ["is_heating"]                   # informativo: não gera evento
        await eng.dish_api.step(force=True)
        assert not db.list_events(types=["alerta_antena"])
        dish.alerts = ["is_heating", "thermal_throttle"]
        await eng.dish_api.step(force=True)
        ev = db.list_events(types=["alerta_antena"])
        assert len(ev) == 1 and ev[0]["details"]["ativados"] == ["Desempenho reduzido por calor"]
        dish.alerts = []
        await eng.dish_api.step(force=True)
        ev = db.list_events(types=["alerta_antena"])
        assert len(ev) == 2 and ev[0]["details"]["encerrados"] == ["Desempenho reduzido por calor"]
        assert eng.dish_api.public()["alerts"] == []

    asyncio.run(scenario())


def test_antena_inacessivel_ou_sem_biblioteca_nao_atrapalha(tmp_path, store):
    async def scenario():
        dish = FakeDish()
        dish.down = True
        eng, db, msgs = make(tmp_path, store, dish)
        await eng.start(run_loops=False)
        for _ in range(3):
            await eng.dish_api.step(force=True)
        assert eng.dish_api.info.status == "indisponivel"
        await eng.run_cycle(eng.session_start + 1000)  # o monitoramento segue normal
        assert eng.conn_status == "online"
        dish.down = False
        await eng.dish_api.step(force=True)
        assert eng.dish_api.info.status == "ativa"

        # outro tipo de conexão: a consulta à antena é desligada
        await eng.set_provider("fibra", "", "")
        await eng.dish_api.step(force=True)
        assert eng.dish_api.info.status == "desativada"

    asyncio.run(scenario())

    async def no_lib():
        dish = FakeDish()
        dish.lib = False
        eng, db, msgs = make(tmp_path / "b", SettingsStore(tmp_path / "b" / "config.json"), dish)
        await eng.start(run_loops=False)
        await eng.dish_api.step(force=True)
        assert eng.dish_api.info.status == "sem_biblioteca"

    (tmp_path / "b").mkdir()
    asyncio.run(no_lib())


def test_api_e_relatorio_com_dados_da_antena(tmp_path, store):
    dish = FakeDish()
    db = Database(tmp_path / "monitor.db")
    now = int(time.time() * 1000)
    dish.outages = [{"start_ts": now - 3_600_000, "duration_ms": 900, "cause": "NO_PINGS", "did_switch": True}]
    app = create_app(starlink_store(store), db, ScriptedProber(), net_reader=FixedNet(),
                     dish_source_factory=lambda host: dish)
    with TestClient(app) as c:
        eng = app.state.engine
        c.portal.call(eng.dish_api.step, True)
        st = c.get("/api/state").json()
        assert st["dish_api"]["status"] == "ativa"
        assert st["dish_api"]["summary_24h"]["count"] == 1
        q = c.get("/api/dish/quedas").json()
        assert q["summary"]["causes"][0]["label"] == "Troca de satélite"
        m = c.get("/api/dish/obstrucao").json()
        assert m["rows"] == 2
        rep = c.get("/relatorio?tipo=personalizado&inicio=%d&fim=%d" % (now - 7_200_000, now))
        assert rep.status_code == 200 and "Troca de satélite" in rep.text
    s0, e0, label = builder.resolve_period("personalizado", None, now - 7_200_000, now)
    assert e0 > s0
