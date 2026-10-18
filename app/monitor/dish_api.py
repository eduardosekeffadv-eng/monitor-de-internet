"""Dados da própria antena Starlink (API local de diagnóstico, somente leitura).

A antena responde em 192.168.100.1:9200 (gRPC) para quem está na rede dela
— pelo Wi-Fi da Starlink, por cabo ou, na maioria dos casos, por um roteador
próprio ligado depois dela. Essa API não é oficial: se a Starlink mudar algo,
o monitor continua funcionando normalmente, apenas sem estes dados extras.

Nunca é enviado nenhum comando que altere a antena (reiniciar, recolher,
limpar mapa etc.). A localização não é consultada.

Estrutura:
* ``StarlinkDishSource`` — conversa com a antena (chamadas bloqueantes,
  executadas em uma thread separada) e devolve dicionários simples.
* ``SimulatedDishSource`` — dados fictícios para o modo simulação.
* ``DishMonitor`` — laço assíncrono que consulta a fonte, grava as quedas
  informadas pela antena e transmite o estado ao painel.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

API_PORT = 9200
GPS_EPOCH_MS = 315_964_800_000          # 06/01/1980 00:00 UTC
GPS_LEAP_MS = 18_000                    # diferença GPS − UTC (segundos bissextos)

STATUS_EVERY_S = 10.0                   # situação atual (normal)
STATUS_FAST_S = 2.0                     # situação atual durante instabilidade/queda
HISTORY_EVERY_S = 60.0                  # quedas e estatísticas
MAP_CACHE_S = 60.0
FAILS_TO_UNAVAILABLE = 3
RETRY_SHORT_S = 60.0
RETRY_LONG_S = 300.0

# --------------------------------------------------------------- motivos
# side: "starlink" = rede/satélites da Starlink · "local" = instalação/antena
CAUSES: dict[str, tuple[str, str, str]] = {
    "NO_PINGS": ("Sem resposta da rede Starlink", "starlink",
                 "A antena estava apontada e sem obstrução, mas deixou de receber resposta da rede da Starlink."),
    "NO_PINGS_SWITCH": ("Troca de satélite", "starlink",
                        "A conexão falhou durante a passagem de um satélite para outro (handover). "
                        "É uma oscilação da própria rede Starlink, não da sua rede local."),
    "OBSTRUCTED": ("Obstrução", "local",
                   "Algo bloqueou a visão do céu da antena (árvore, telhado, parede, antena mal posicionada). "
                   "Veja o mapa de obstruções no painel."),
    "NO_SCHEDULE": ("Sem satélite disponível", "starlink",
                    "A rede da Starlink não tinha satélite agendado para a sua antena naquele momento "
                    "(falta de cobertura ou capacidade)."),
    "NO_SATS": ("Nenhum satélite visível", "starlink",
                "A antena não encontrou satélites disponíveis naquele momento."),
    "NO_DOWNLINK": ("Sem sinal de descida", "starlink",
                    "A antena perdeu o sinal vindo do satélite."),
    "BOOTING": ("Antena reiniciando", "local",
                "A antena estava ligando ou reiniciando (queda de energia, reinício pelo app ou atualização)."),
    "STOWED": ("Antena recolhida", "local", "A antena estava recolhida (modo de transporte)."),
    "THERMAL_SHUTDOWN": ("Desligamento por temperatura", "local",
                         "A antena se desligou por excesso de calor."),
    "SLEEPING": ("Modo de economia", "local",
                 "A antena estava em horário de economia de energia configurado no app."),
    "CABLE_RESET": ("Reinício do cabo / alimentação", "local",
                    "A conexão do cabo ou a alimentação da antena foi reiniciada."),
    "ACTUATOR_ACTIVITY": ("Antena se reposicionando", "local",
                          "Os motores estavam reposicionando a antena."),
    "MOVING_WHILE_NOT_PERMITTED": ("Antena em movimento", "local",
                                   "A antena estava em movimento, o que o plano contratado não permite."),
    "UNKNOWN": ("Motivo não informado", "indeterminado", "A antena registrou a queda sem informar o motivo."),
}
# numeração do protocolo da antena (usada se o nome não vier na resposta)
CAUSE_BY_NUMBER = {0: "UNKNOWN", 1: "BOOTING", 2: "STOWED", 3: "THERMAL_SHUTDOWN", 4: "NO_SCHEDULE", 5: "NO_SATS",
                   6: "OBSTRUCTED", 7: "NO_DOWNLINK", 8: "NO_PINGS", 9: "ACTUATOR_ACTIVITY", 10: "CABLE_RESET",
                   11: "SLEEPING", 12: "MOVING_WHILE_NOT_PERMITTED"}
SIDE_LABEL = {"starlink": "Rede Starlink", "local": "Instalação / antena", "indeterminado": "Indeterminado"}

STATE_LABEL = {
    "CONNECTED": "Conectada",
    "SEARCHING": "Procurando satélite",
    "BOOTING": "Iniciando",
    "STOWED": "Recolhida",
    "THERMAL_SHUTDOWN": "Desligada por temperatura",
    "OBSTRUCTED": "Obstruída",
    "NO_PINGS": "Sem resposta da rede",
    "NO_SATS": "Sem satélites",
    "NO_DOWNLINK": "Sem sinal",
    "SLEEPING": "Em modo de economia",
    "UNKNOWN": "Desconhecido",
}

ALERT_LABEL = {
    "motors_stuck": "Motores travados",
    "thermal_throttle": "Desempenho reduzido por calor",
    "thermal_shutdown": "Desligamento por calor",
    "mast_not_near_vertical": "Mastro fora do prumo",
    "unexpected_location": "Localização diferente da cadastrada",
    "slow_ethernet_speeds": "Cabo de rede lento",
    "slow_ethernet_speeds_100": "Cabo de rede limitado a 100 Mb/s",
    "install_pending": "Atualização pendente",
    "is_heating": "Aquecendo (derretendo neve)",
    "power_supply_thermal_throttle": "Fonte reduzindo por calor",
    "is_power_save_idle": "Em economia de energia",
    "dbf_telem_stale": "Telemetria interna desatualizada",
    "low_motor_current": "Corrente baixa nos motores",
    "lower_signal_than_predicted": "Sinal abaixo do esperado",
    "obstruction_map_reset": "Mapa de obstruções reiniciado",
    "dish_water_detected": "Água detectada na antena",
    "router_water_detected": "Água detectada no roteador",
    "upsu_router_port_slow": "Porta do roteador lenta",
    "no_ethernet_link": "Sem link de rede",
}
# Alertas que só informam um estado, sem indicar problema.
ALERT_INFO_ONLY = {"is_heating", "is_power_save_idle", "install_pending", "obstruction_map_reset"}


def cause_key(cause: str, did_switch: bool) -> str:
    cause = (cause or "UNKNOWN").upper()
    if cause == "NO_PINGS" and did_switch:
        return "NO_PINGS_SWITCH"
    return cause if cause in CAUSES else "UNKNOWN"


def cause_info(cause: str, did_switch: bool = False) -> dict:
    key = cause_key(cause, did_switch)
    label, side, text = CAUSES[key]
    if key == "UNKNOWN" and cause and cause.upper() != "UNKNOWN":
        label = f"Motivo informado pela antena: {cause}"
    return {"cause": key, "cause_raw": cause, "label": label, "side": side,
            "side_label": SIDE_LABEL[side], "text": text}


def state_label(state: Optional[str]) -> str:
    if not state:
        return "—"
    return STATE_LABEL.get(state.upper(), state)


def alert_label(name: str) -> str:
    return ALERT_LABEL.get(name, name.replace("_", " "))


def gps_ns_to_ms(ns: int) -> int:
    return int(round(ns / 1e6)) + GPS_EPOCH_MS - GPS_LEAP_MS


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def mask_id(did: Any) -> str:
    s = str(did or "")
    return (s[:6] + "…" + s[-4:]) if len(s) > 12 else ""


# ------------------------------------------------------------ fontes
class DishUnavailable(Exception):
    """A antena não respondeu ou a API mudou."""


class StarlinkDishSource:
    """Consulta a antena real. Métodos bloqueantes (use em thread)."""

    simulated = False

    def __init__(self, host: str) -> None:
        self.host = host
        self._ctx = None
        self._mod = None

    @staticmethod
    def library_available() -> bool:
        try:
            import starlink_grpc  # noqa: F401
            return True
        except Exception:
            return False

    def _lib(self):
        try:
            if self._mod is None:
                import starlink_grpc
                starlink_grpc.REQUEST_TIMEOUT = 5
                self._mod = starlink_grpc
            if self._ctx is None:
                self._ctx = self._mod.ChannelContext(target=f"{self.host}:{API_PORT}")
        except Exception as exc:
            raise self._fail(exc)
        return self._mod, self._ctx

    def close(self) -> None:
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None

    def _fail(self, exc: Exception) -> DishUnavailable:
        self.close()   # recria o canal na próxima tentativa
        return DishUnavailable(str(exc) or exc.__class__.__name__)

    def status(self) -> dict:
        sg, ctx = self._lib()
        try:
            st, obst, alerts = sg.status_data(ctx)
        except Exception as exc:
            raise self._fail(exc)
        active = sorted(k[6:] for k, v in alerts.items() if v and k.startswith("alert_"))
        return {
            "state": st.get("state") or "UNKNOWN",
            "uptime_s": _num(st.get("uptime")),
            "hardware": st.get("hardware_version") or "",
            "software": st.get("software_version") or "",
            "id_masked": mask_id(st.get("id")),
            "pop_latency_ms": _num(st.get("pop_ping_latency_ms")),
            "drop_rate": _num(st.get("pop_ping_drop_rate")),
            "down_bps": _num(st.get("downlink_throughput_bps")),
            "up_bps": _num(st.get("uplink_throughput_bps")),
            "obstructed_now": st.get("currently_obstructed"),
            "fraction_obstructed": _num(st.get("fraction_obstructed")),
            "obstruction_duration_s": _num(st.get("obstruction_duration")),
            "obstruction_interval_s": _num(st.get("obstruction_interval")),
            "seconds_to_slot": _num(st.get("seconds_to_first_nonempty_slot")),
            "alerts": active,
            "alerts_known": sorted(k[6:] for k in alerts if k.startswith("alert_")),
        }

    def history(self, start_counter: Optional[int]) -> dict:
        """Quedas informadas pela antena + estatísticas desde ``start_counter``."""
        sg, ctx = self._lib()
        try:
            hist = sg.get_history(ctx)
        except Exception as exc:
            raise self._fail(exc)
        outages = []
        for o in list(getattr(hist, "outages", []) or []):
            try:
                try:
                    cause = o.DESCRIPTOR.fields_by_name["cause"].enum_type.values_by_number[o.cause].name
                except Exception:
                    raw = getattr(o, "cause", 0)
                    cause = CAUSE_BY_NUMBER.get(raw, str(raw)) if isinstance(raw, int) else str(raw)
                start_ns = int(getattr(o, "start_timestamp_ns", 0) or 0)
                if start_ns <= 0:
                    continue
                outages.append({"start_ts": gps_ns_to_ms(start_ns),
                                "duration_ms": int(round(int(getattr(o, "duration_ns", 0) or 0) / 1e6)),
                                "cause": cause, "did_switch": bool(getattr(o, "did_switch", False))})
            except Exception:
                continue
        window = None
        end_counter = None
        try:
            if start_counter is None:
                g, *_ = sg.history_stats(1, history=hist)
                end_counter = g.get("end_counter")
            else:
                g, drop, _rl, lat, _ld, usage, power = sg.history_stats(-1, start=start_counter, history=hist)
                end_counter = g.get("end_counter")
                n = int(g.get("samples") or 0)
                if n > 0:
                    window = {
                        "samples": n,
                        "drop_s": _num(drop.get("total_ping_drop")) or 0.0,
                        "full_drop_s": int(drop.get("count_full_ping_drop") or 0),
                        "obstructed_s": _num(drop.get("total_obstructed_ping_drop")) or 0.0,
                        "full_obstructed_s": int(drop.get("count_full_obstructed_ping_drop") or 0),
                        "unscheduled_s": _num(drop.get("total_unscheduled_ping_drop")) or 0.0,
                        "full_unscheduled_s": int(drop.get("count_full_unscheduled_ping_drop") or 0),
                        "lat_mean_ms": _num(lat.get("mean_all_ping_latency")),
                        "power_w": _num(power.get("mean_power")) if power else None,
                        "down_bytes": int(usage.get("download_usage") or 0) if usage else 0,
                        "up_bytes": int(usage.get("upload_usage") or 0) if usage else 0,
                    }
        except Exception as exc:
            log.debug("Estatísticas da antena indisponíveis: %s", exc)
        return {"outages": outages, "end_counter": end_counter, "window": window}

    def obstruction_map(self) -> dict:
        sg, ctx = self._lib()
        try:
            rows = sg.obstruction_map(ctx)
        except Exception as exc:
            raise self._fail(exc)
        data = [[round(float(v), 2) for v in r] for r in rows]
        return {"rows": len(data), "cols": len(data[0]) if data else 0, "data": data}


class SimulatedDishSource:
    """Antena fictícia (modo simulação): espelha as quedas do monitor com motivos variados."""

    simulated = True

    def __init__(self, recent_failures: Callable[[int], list[dict]]) -> None:
        self._recent = recent_failures
        self._rng = random.Random(7)
        self._boot = time.time() - 2.9 * 86400
        self._counter = 0
        self._outages: dict[int, dict] = {}
        self._mirrored: set[int] = set()

    @staticmethod
    def library_available() -> bool:
        return True

    def close(self) -> None:
        pass

    def status(self) -> dict:
        t = time.time()
        rng = self._rng
        return {
            "state": "CONNECTED", "uptime_s": t - self._boot, "hardware": "mini1_simulada",
            "software": "simulacao", "id_masked": "ut0000…0000",
            "pop_latency_ms": 24 + 4 * math.sin(t / 90) + rng.gauss(0, 1.5),
            "drop_rate": max(0.0, rng.gauss(0.002, 0.003)),
            "down_bps": abs(rng.gauss(2e6, 1.5e6)), "up_bps": abs(rng.gauss(3e5, 2e5)),
            "obstructed_now": False, "fraction_obstructed": 0.012,
            "obstruction_duration_s": None, "obstruction_interval_s": None, "seconds_to_slot": 0.0,
            "alerts": [], "alerts_known": sorted(ALERT_LABEL),
        }

    def history(self, start_counter: Optional[int]) -> dict:
        rng = self._rng
        now = int(time.time() * 1000)
        for ev in self._recent(now - 3 * 3600 * 1000):
            if ev["id"] in self._mirrored or ev.get("open"):
                continue
            self._mirrored.add(ev["id"])
            if rng.random() < 0.15:       # algumas quedas só o monitor percebe (ex.: Wi-Fi)
                continue
            cause, sw = rng.choices([("NO_PINGS", True), ("OBSTRUCTED", False), ("NO_SCHEDULE", False),
                                     ("NO_PINGS", False)], weights=[60, 20, 12, 8])[0]
            start = ev["start_ts"] + rng.randint(-600, 600)
            dur = max(300, (ev["duration_ms"] or 1000) + rng.randint(-500, 300))
            self._outages[start] = {"start_ts": start, "duration_ms": dur, "cause": cause, "did_switch": sw}
        if rng.random() < 0.08:            # quedas curtas que o monitor não chega a ver
            start = now - rng.randint(5_000, 50_000)
            self._outages[start] = {"start_ts": start, "duration_ms": rng.randint(200, 900),
                                    "cause": "NO_PINGS", "did_switch": True}
        self._outages = {k: v for k, v in self._outages.items() if k > now - 12 * 3600 * 1000}
        prev = self._counter
        self._counter += int(HISTORY_EVERY_S)
        window = None
        if start_counter is not None:
            n = self._counter - prev
            window = {"samples": n, "drop_s": round(abs(rng.gauss(0.3, 0.3)), 2), "full_drop_s": 0,
                      "obstructed_s": round(abs(rng.gauss(0.05, 0.05)), 2), "full_obstructed_s": 0,
                      "unscheduled_s": 0.0, "full_unscheduled_s": 0, "lat_mean_ms": 25 + rng.gauss(0, 2),
                      "power_w": 18 + rng.gauss(0, 1), "down_bytes": int(abs(rng.gauss(2e7, 1e7))),
                      "up_bytes": int(abs(rng.gauss(3e6, 1e6)))}
        return {"outages": sorted(self._outages.values(), key=lambda o: o["start_ts"]),
                "end_counter": self._counter, "window": window}

    def obstruction_map(self) -> dict:
        n = 123
        c = (n - 1) / 2
        rng = random.Random(42)
        data = []
        for y in range(n):
            row = []
            for x in range(n):
                r = math.hypot(x - c, y - c) / c
                ang = math.degrees(math.atan2(y - c, x - c)) % 360
                # trilhas de satélites já observadas (faixas inclinadas) + região central
                track = (math.sin((x * 0.9 + y * 0.35) / 3.2) > 0.55) or (math.sin((x * 0.3 - y) / 4.1) > 0.75)
                if r > 1 or not (r < 0.3 or track) or rng.random() < 0.15:
                    row.append(-1.0)       # direção ainda não observada
                elif 200 < ang < 240 and r > 0.68:
                    row.append(round(rng.uniform(0, 0.4), 2))   # obstrução (ex.: árvore)
                else:
                    row.append(1.0)
            data.append(row)
        return {"rows": n, "cols": n, "data": data}


# ----------------------------------------------------------- monitor
@dataclass
class DishInfo:
    status: str = "desativada"   # desativada | sem_biblioteca | conectando | ativa | indisponivel
    message: str = ""
    last_ok: Optional[int] = None
    data: dict = field(default_factory=dict)
    fails: int = 0


class DishMonitor:
    """Consulta periódica da antena, dentro do processo do monitor."""

    def __init__(self, engine: Any, source_factory: Optional[Callable[[str], Any]] = None) -> None:
        self.engine = engine
        self._factory = source_factory
        self.source: Any = None
        self._source_host: Optional[str] = None
        self.info = DishInfo()
        self.counter: Optional[int] = None
        self._next_status = 0.0
        self._next_history = 0.0
        self._retry_at = 0.0
        self._map_cache: Optional[tuple[float, dict]] = None
        self._map_lock = asyncio.Lock()
        self._kick = asyncio.Event()
        self._known_alerts: Optional[set[str]] = None

    # ----------------------------------------------------------- estado
    def wanted(self) -> bool:
        s = self.engine.s
        dish = s.target("dish")
        return (s.provider_type == "starlink" and s.dish_api_enabled and dish.enabled and bool(dish.host))

    def kick(self) -> None:
        self._kick.set()

    def public(self) -> dict:
        i = self.info
        d = dict(i.data)
        st = d.get("state")
        return {
            "status": i.status, "message": i.message, "last_ok": i.last_ok,
            "simulated": bool(getattr(self.source, "simulated", False)),
            "state": st, "state_label": state_label(st), "connected": st == "CONNECTED",
            "uptime_s": d.get("uptime_s"), "hardware": d.get("hardware"), "software": d.get("software"),
            "id_masked": d.get("id_masked"),
            "pop_latency_ms": d.get("pop_latency_ms"), "drop_rate": d.get("drop_rate"),
            "down_bps": d.get("down_bps"), "up_bps": d.get("up_bps"),
            "obstructed_now": d.get("obstructed_now"), "fraction_obstructed": d.get("fraction_obstructed"),
            "power_w": d.get("power_w"),
            "alerts": [{"id": a, "label": alert_label(a), "info": a in ALERT_INFO_ONLY}
                       for a in d.get("alerts", [])],
            "summary_24h": self.engine.db.dish_cause_summary(int(time.time() * 1000) - 86400_000,
                                                             int(time.time() * 1000)),
        }

    def _make_source(self, host: str) -> Any:
        if self._factory is not None:
            return self._factory(host)
        if getattr(self.engine, "simulated", False):
            return SimulatedDishSource(self._recent_failures)
        return StarlinkDishSource(host)

    def _recent_failures(self, since: int) -> list[dict]:
        return self.engine.db.list_events(types=["interrupcao", "microqueda"], since=since, limit=500,
                                          now_ms=int(time.time() * 1000), attach_dish=False)

    def _set(self, status: str, message: str = "") -> bool:
        changed = (status, message) != (self.info.status, self.info.message)
        self.info.status, self.info.message = status, message
        return changed

    # ------------------------------------------------------------- laço
    async def run(self) -> None:
        while True:
            try:
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Erro ao consultar a antena (o monitoramento continua)")
            self._kick.clear()
            try:
                await asyncio.wait_for(self._kick.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    async def step(self, force: bool = False) -> None:
        """Um passo do laço (os testes chamam diretamente com force=True)."""
        mono = time.monotonic()
        if not self.wanted():
            if self.source is not None:
                self.source.close()
                self.source = None
            if self._set("desativada"):
                self.info.data = {}
                await self._emit()
            return
        host = self.engine.s.target("dish").host
        if self.source is None or self._source_host != host:
            if self.source is not None:
                self.source.close()
            self.source = self._make_source(host)
            self._source_host = host
            self.counter = None
            self._known_alerts = None
            self._next_status = self._next_history = self._retry_at = 0.0
            if not self.source.library_available():
                self._set("sem_biblioteca", "Biblioteca de comunicação com a antena não instalada "
                                            "(execute o instalar.bat novamente).")
                await self._emit()
                return
            self._set("conectando", "Conectando à antena…")
            await self._emit()
        if self.info.status == "sem_biblioteca":
            return
        if not force and mono < self._retry_at:
            return

        if force or mono >= self._next_status:
            fast = getattr(self.engine, "conn_status", "online") != "online"
            self._next_status = mono + (STATUS_FAST_S if fast else STATUS_EVERY_S)
            try:
                st = await asyncio.to_thread(self.source.status)
            except DishUnavailable as exc:
                await self._failed(str(exc), mono)
                return
            await self._status_ok(st)

        if force or mono >= self._next_history:
            self._next_history = mono + HISTORY_EVERY_S
            try:
                h = await asyncio.to_thread(self.source.history, self.counter)
            except DishUnavailable as exc:
                await self._failed(str(exc), mono)
                return
            await self._history_ok(h)

    async def _failed(self, err: str, mono: float) -> None:
        self.info.fails += 1
        log.debug("Antena sem resposta (%d): %s", self.info.fails, err)
        if self.info.fails >= FAILS_TO_UNAVAILABLE:
            wait = RETRY_SHORT_S if self.info.fails < 10 else RETRY_LONG_S
            self._retry_at = mono + wait
            if self._set("indisponivel", "A antena não respondeu à consulta de dados. Nova tentativa "
                                         f"em {int(wait // 60)} min (o monitoramento continua normalmente)."):
                log.info("Dados da antena indisponíveis: %s", err)
                await self._emit()

    async def _status_ok(self, st: dict) -> None:
        first = self.info.status != "ativa"
        self.info.fails = 0
        prev_power = self.info.data.get("power_w")
        self.info.data = {**st, "power_w": prev_power}
        self.info.last_ok = int(time.time() * 1000)
        if first:
            self._set("ativa")
            log.info("Dados da antena Starlink disponíveis (%s, software %s)", st.get("hardware"),
                     st.get("software"))
        await self._check_alerts(set(st.get("alerts") or []))
        await self._emit()

    async def _check_alerts(self, active: set[str]) -> None:
        if self._known_alerts is None:
            self._known_alerts = active
            # alertas já ativos ao começar também ficam registrados
            if not any(a not in ALERT_INFO_ONLY for a in active):
                return
            on, off = active, set()
        else:
            on, off = active - self._known_alerts, self._known_alerts - active
            self._known_alerts = active
        on = {a for a in on if a not in ALERT_INFO_ONLY}
        off = {a for a in off if a not in ALERT_INFO_ONLY}
        if not on and not off:
            return
        details = {}
        if on:
            details["ativados"] = [alert_label(a) for a in sorted(on)]
        if off:
            details["encerrados"] = [alert_label(a) for a in sorted(off)]
        payload = self.engine.dish_alert_event(details)
        await self.engine.emit({"type": "event", "event": payload})
        log.info("Alertas da antena: %s", details)

    async def _history_ok(self, h: dict) -> None:
        db = self.engine.db
        if h.get("window"):
            if h["window"].get("power_w") is not None:
                self.info.data["power_w"] = h["window"]["power_w"]
            db.insert_dish_stats(int(time.time() * 1000), h["window"])
        if h.get("end_counter") is not None:
            self.counter = int(h["end_counter"])
        new = db.insert_dish_outages(h.get("outages") or [], int(time.time() * 1000))
        db.commit()
        if new:
            lo = min(o["start_ts"] for o in new)
            hi = max(o["start_ts"] + o["duration_ms"] for o in new)
            for ev in db.list_events(types=["interrupcao", "microqueda"], since=lo - 10_000, until=hi + 10_000,
                                     limit=200, now_ms=int(time.time() * 1000)):
                await self.engine.emit({"type": "event", "event": ev})
            log.info("Antena informou %d queda(s) nova(s)", len(new))
        await self._emit()

    async def _emit(self) -> None:
        await self.engine.emit({"type": "dish", "dish": self.public()})

    async def obstruction_map(self) -> dict:
        if self.info.status != "ativa" or self.source is None:
            raise DishUnavailable("dados da antena indisponíveis no momento")
        async with self._map_lock:
            if self._map_cache and time.monotonic() - self._map_cache[0] < MAP_CACHE_S:
                return self._map_cache[1]
            m = await asyncio.to_thread(self.source.obstruction_map)
            m["fetched_at"] = int(time.time() * 1000)
            self._map_cache = (time.monotonic(), m)
            return m

    def close(self) -> None:
        if self.source is not None:
            self.source.close()
