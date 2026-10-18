"""Motor de monitoramento: agenda os ciclos, mede, detecta, grava e transmite.

Roda dentro do mesmo processo do servidor web, em tarefas assíncronas.
Fechar o Chrome não interfere em nada; o monitoramento só para quando o
processo é encerrado (parar_monitoramento.bat, desligamento do Windows,
ou falha — que fica registrada no log).
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import socket
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from .. import __version__
from ..config import CONNECTION_MODES, EXTERNAL_TARGETS, TARGET_IDS, WIFI_MODES, SettingsStore
from ..events.detector import (E_CLOCK, E_DISH_ALERT, E_INACTIVE, E_MODE, E_NETCHANGE, E_PROVIDER, E_START, E_STOP,
                               Detector, Event)
from ..providers import PROVIDERS, profile, public_profiles
from ..services.stats import compute_stats
from ..storage.db import CONN_CODE, Database
from . import power
from .dish_api import DishMonitor
from .netinfo import NetInfoReader
from .types import M_FAIL, Cycle, NetInfo, ProbeResult, WifiInfo
from .wifi import read_wifi

log = logging.getLogger(__name__)

RETROACTIVE_MODE_WINDOW_MS = 10 * 60 * 1000   # escolha de modo logo após iniciar vale desde o início
DISH_DETECT_ATTEMPTS = 3
DISH_LOST_STREAK = 120                         # ~2 min sem resposta (com internet ok) → desativa
WIFI_REFRESH_S = 10.0
STATS_REFRESH_S = 5.0
LIVE_BUFFER = 1900                               # amostras mantidas em memória para o painel


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class DishState:
    status: str = "detectando"     # detectando | ativa | indisponivel | desativada
    attempts: int = 0
    fail_streak: int = 0
    next_retry: float = 0.0


class MonitorEngine:
    def __init__(self, store: SettingsStore, db: Database, prober: Any,
                 broadcast: Callable[[dict], Awaitable[None] | None],
                 net_reader: Any = None, dish_source_factory: Any = None) -> None:
        self.store = store
        self.db = db
        self.prober = prober
        self.simulated = bool(getattr(prober, "simulated", False))
        self._broadcast = broadcast
        self.detector = Detector(store.settings)
        self.netreader = net_reader or NetInfoReader()
        self.session_id: Optional[int] = None
        self.session_start = now_ms()
        self.mode = store.settings.last_mode
        self.mode_period_id: Optional[int] = None
        self.mode_confirmed = False
        self.net = NetInfo()
        self.wifi: Optional[WifiInfo] = None
        self._wifi_kick = asyncio.Event()
        self.dish = DishState()
        self.last_cycle: Optional[dict] = None
        self.live: list[dict] = []
        self.stats: dict = {}
        self.conn_status = "online"
        self.loss_pct: Optional[float] = None
        self.lat_median: Optional[float] = None
        self.cycle_count = 0
        self.started = False
        self._tasks: list[asyncio.Task] = []
        self._last_power_refresh = 0.0
        self._recovery_info: dict = {}
        self._running_port = store.settings.port
        self.dish_api = DishMonitor(self, dish_source_factory)

    # ------------------------------------------------------------- util
    @property
    def s(self):
        return self.store.settings

    async def emit(self, msg: dict) -> None:
        try:
            r = self._broadcast(msg)
            if asyncio.iscoroutine(r):
                await r
        except Exception:
            log.exception("Falha ao transmitir mensagem ao painel")

    def _persist_changes(self, changes: list) -> list[dict]:
        out = []
        for action, ev in changes:
            if action in ("open", "instant"):
                self.db.insert_event(ev, self.session_id)
            else:
                self.db.update_event(ev)
            out.append(self._event_payload(ev))
        return out

    def _event_payload(self, ev: Event) -> dict:
        row = self.db.conn.execute("SELECT * FROM events WHERE id=?", (ev.id,)).fetchone()
        if not row:
            return {}
        d = self.db.event_to_dict(row, now_ms())
        self.db.attach_dish([d], now_ms())
        return d

    def dish_alert_event(self, details: dict) -> dict:
        p = self._instant_event(E_DISH_ALERT, now_ms(), details)
        self.db.commit()
        return p

    def _instant_event(self, etype: str, ts: int, details: dict, end_ts: Optional[int] = None) -> dict:
        ev = Event(type=etype, start_ts=ts, end_ts=ts if end_ts is None else end_ts,
                   mode=self.mode, mode_period_id=self.mode_period_id, details=details)
        self.db.insert_event(ev, self.session_id)
        return self._event_payload(ev)

    # ---------------------------------------------------- ciclo de vida
    async def start(self, run_loops: bool = True) -> None:
        ts = now_ms()
        info = self.db.recover_previous()
        self._recovery_info = info
        if info.get("crashed"):
            log.warning("A execução anterior terminou de forma inesperada; %d evento(s) aberto(s) foram encerrados.",
                        info.get("closed_events", 0))
        self.session_start = ts
        self.session_id = self.db.start_session(ts, self.s.interval_ms, __version__, self.simulated,
                                                self.s.provider_type)

        last_ts = info.get("last_ts")
        if last_ts is not None and ts - last_ts > self.s.gap_threshold_s * 1000:
            reason = ("programa encerrado sem aviso (ex.: Windows desligado ou processo finalizado)" if info.get("crashed")
                      else "programa fechado ou computador desligado")
            self._instant_event(E_INACTIVE, last_ts, {"motivo": reason, "precisao": "início = última medição"},
                                end_ts=ts)

        self.mode = self.s.last_mode
        self.mode_confirmed = False
        self.mode_period_id = self.db.open_mode_period(self.mode, ts, self.session_id, confirmed=False)
        self._instant_event(E_START, ts, {"versao": __version__, "simulacao": self.simulated,
                                          "intervalo_ms": self.s.interval_ms})
        self.db.commit()

        if self.s.prevent_sleep and not self.simulated:
            power.set_prevent_sleep(True)
            self._last_power_refresh = time.monotonic()

        if not self.s.target("dish").enabled:
            self.dish.status = "desativada"

        self.started = True
        if not run_loops:   # usado pelos testes automatizados, que conduzem os ciclos
            return
        loop = asyncio.get_running_loop()
        self._tasks = [
            loop.create_task(self._cycle_loop(), name="ciclos"),
            loop.create_task(self._wifi_loop(), name="wifi"),
            loop.create_task(self._stats_loop(), name="estatisticas"),
            loop.create_task(self._maintenance_loop(), name="manutencao"),
            loop.create_task(self.dish_api.run(), name="antena"),
        ]
        log.info("Monitoramento iniciado (sessão %s, intervalo %d ms, simulação=%s)",
                 self.session_id, self.s.interval_ms, self.simulated)

    async def stop(self, reason: str = "encerrado pelo usuário") -> None:
        if not self.started:
            return
        self.started = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        ts = now_ms()
        last = self.detector.last_ts or ts
        try:
            self._persist_changes(self.detector.interrupt(last, "monitoramento encerrado"))
            self._instant_event(E_STOP, ts, {"motivo": reason})
            if self.mode_period_id:
                self.db.close_mode_period(self.mode_period_id, ts)
            self.db.end_session(self.session_id, ts, reason)
            self.db.commit()
        except Exception:
            log.exception("Erro ao registrar o encerramento")
        if not self.simulated:
            power.set_prevent_sleep(False)
        try:
            self.prober.close()
        except Exception:
            pass
        self.dish_api.close()
        log.info("Monitoramento encerrado: %s", reason)

    # ----------------------------------------------------------- modos
    async def set_mode(self, mode: str) -> dict:
        if mode not in CONNECTION_MODES:
            raise ValueError("modo de conexão inválido")
        ts = now_ms()
        previous = self.mode
        if not self.mode_confirmed and ts - self.session_start <= RETROACTIVE_MODE_WINDOW_MS:
            # Informado logo após iniciar: vale para toda a sessão até aqui.
            self.db.update_mode_period(self.mode_period_id, mode, confirmed=True)
            self.mode = mode
        elif mode == self.mode:
            self.db.update_mode_period(self.mode_period_id, mode, confirmed=True)
        else:
            self.db.close_mode_period(self.mode_period_id, ts)
            self.mode_period_id = self.db.open_mode_period(mode, ts, self.session_id, confirmed=True)
            self.mode = mode
        self.mode_confirmed = True
        events = []
        if previous != mode:
            events.append(self._instant_event(E_MODE, ts, {
                "de": CONNECTION_MODES[previous], "para": CONNECTION_MODES[mode]}))
        self.db.commit()
        self.store.update(last_mode=mode)
        if mode in WIFI_MODES:
            self._wifi_kick.set()
        payload = self.mode_info()
        await self.emit({"type": "mode", "mode": payload})
        for e in events:
            await self.emit({"type": "event", "event": e})
        log.info("Modo de conexão: %s (anterior: %s)", mode, previous)
        return payload

    # ------------------------------------------------------ configurações
    EDITABLE = ("interval_ms", "timeout_ms", "lat_normal_max_ms", "lat_elevated_max_ms", "loss_threshold_pct",
                "loss_window_s", "loss_min_cycles", "latency_window_cycles", "outage_confirm_cycles",
                "wifi_weak_signal_pct", "gap_threshold_s", "retention_raw_days", "retention_agg_days",
                "prevent_sleep", "autostart", "dish_retry_minutes", "dish_api_enabled", "chart_window_s", "port",
                "targets")

    def settings_info(self) -> dict:
        from ..system import autostart
        st = self.s.to_dict()
        st["autostart"] = autostart.is_enabled() if autostart.supported() else False
        return {
            "settings": st,
            "defaults": type(self.s)().to_dict(),
            "provider": self.provider_info(),
            "autostart_supported": autostart.supported(),
            "restart_fields": ["port"],
            "running_port": self._running_port,
            "db": {"path": str(self.db.path), "bytes": self.db.size_bytes(),
                   "first_ts": self.db.first_sample_ts()},
            "simulated": self.simulated,
        }

    async def update_settings(self, changes: dict) -> dict:
        """Aplica alterações vindas da página de configurações (valida tudo)."""
        from ..system import autostart
        changes = {k: v for k, v in changes.items() if k in self.EDITABLE}
        before = self.s.to_dict()
        merged = dict(before)
        if "targets" in changes and isinstance(changes["targets"], list):
            by_id = {t["id"]: dict(t) for t in before["targets"]}
            for t in changes["targets"]:
                if isinstance(t, dict) and t.get("id") in by_id:
                    by_id[t["id"]].update({k: t[k] for k in ("host", "enabled", "tcp_port") if k in t})
            merged["targets"] = list(by_id.values())
            changes = {k: v for k, v in changes.items() if k != "targets"}
        merged.update(changes)
        self.store.update(**merged)
        after = self.s.to_dict()

        warnings = []
        for k, v in changes.items():
            if k in after and after[k] != v:
                warnings.append(f"\"{k}\" ajustado para {after[k]} (valor informado fora dos limites ou inconsistente).")
        self.detector.apply_settings(self.s)
        if before["interval_ms"] != after["interval_ms"] and self.session_id is not None:
            # O tempo monitorado é calculado por sessão (ciclos × intervalo): um novo
            # intervalo começa uma nova sessão, sem lacuna.
            ts = now_ms()
            self.db.end_session(self.session_id, ts, "intervalo de medição alterado")
            self.session_id = self.db.start_session(ts, after["interval_ms"], __version__, self.simulated,
                                                    self.s.provider_type)
            self.mode_period_id = self.db.open_mode_period(self.mode, ts, self.session_id, self.mode_confirmed)
        if before["prevent_sleep"] != after["prevent_sleep"] and not self.simulated:
            power.set_prevent_sleep(after["prevent_sleep"])
            self._last_power_refresh = time.monotonic()
        if autostart.supported() and "autostart" in changes:
            final = autostart.set_enabled(bool(changes["autostart"]))
            if final != bool(changes["autostart"]):
                warnings.append("Não foi possível alterar o início automático com o Windows (veja o log).")
        dish_b = next(t for t in before["targets"] if t["id"] == "dish")
        dish_a = next(t for t in after["targets"] if t["id"] == "dish")
        if (dish_b["host"], dish_b["enabled"]) != (dish_a["host"], dish_a["enabled"]):
            self.dish = DishState(status="detectando" if dish_a["enabled"] else "desativada")
        self.dish_api.kick()
        restart = after["port"] != self._running_port
        changed = sorted(k for k in after if before.get(k) != after.get(k))
        if changed:
            e = self._instant_event("configuracoes_alteradas", now_ms(), {"alterados": changed})
            self.db.commit()
            await self.emit({"type": "event", "event": e})
            log.info("Configurações alteradas: %s", ", ".join(changed))
        await self.emit({"type": "provider", "provider": self.provider_info(), "targets": self.targets_info(),
                         "settings": self._settings_public()})
        info = self.settings_info()
        info["warnings"] = warnings
        info["restart_needed"] = restart
        info["changed"] = changed
        return info

    # --------------------------------------------------- tipo de conexão
    def provider_info(self) -> dict:
        prof = profile(self.s.provider_type)
        dish = self.s.target("dish")
        return {
            "type": self.s.provider_type,
            "configured": bool(self.s.provider_type),
            "label": prof.label if self.s.provider_type else "Não definido",
            "name": self.s.provider_name,
            "equip_name": dish.name,
            "equip_short": prof.equip_short if self.s.provider_type else "Provedor",
            "equip_host": dish.host,
            "equip_enabled": dish.enabled,
            "lat_normal_max_ms": self.s.lat_normal_max_ms,
            "lat_elevated_max_ms": self.s.lat_elevated_max_ms,
            "profiles": public_profiles(),
        }

    async def set_provider(self, ptype: str, name: str = "", equip_host: Optional[str] = None,
                           apply_thresholds: bool = True) -> dict:
        if ptype not in PROVIDERS:
            raise ValueError("tipo de conexão inválido")
        prof = PROVIDERS[ptype]
        previous = self.s.provider_type
        host = prof.default_host if equip_host is None else equip_host.strip()
        if host:
            try:
                socket.inet_aton(host)
                if host.count(".") != 3:
                    raise OSError
            except OSError:
                raise ValueError("endereço IPv4 inválido para o equipamento do provedor")
        data = self.s.to_dict()
        data["provider_type"] = ptype
        data["provider_name"] = name
        for t in data["targets"]:
            if t["id"] == "dish":
                t.update(name=prof.equip_name, host=host, tcp_port=prof.tcp_port, enabled=bool(host))
        if apply_thresholds:
            data["lat_normal_max_ms"] = prof.lat_normal_ms
            data["lat_elevated_max_ms"] = prof.lat_elevated_ms
        self.store.update(**data)
        self.detector.apply_settings(self.s)
        self.dish = DishState(status="detectando" if host else "desativada")
        self.db.set_session_provider(self.session_id, ptype)
        self.dish_api.kick()
        # registrado sempre, inclusive quando muda só o endereço ou o nome do provedor
        events = [self._instant_event(E_PROVIDER, now_ms(), {
            "de": PROVIDERS[previous].label if previous in PROVIDERS else "não definido",
            "para": prof.label, "provedor": name, "equipamento": prof.equip_name,
            "endereco_equipamento": host or "não monitorado"})]
        self.db.commit()
        info = self.provider_info()
        await self.emit({"type": "provider", "provider": info, "targets": self.targets_info(),
                         "settings": self._settings_public()})
        for e in events:
            await self.emit({"type": "event", "event": e})
        log.info("Tipo de conexão: %s (anterior: %s), equipamento: %s", ptype, previous or "-", host or "-")
        return info

    def mode_info(self) -> dict:
        return {
            "mode": self.mode,
            "label": CONNECTION_MODES[self.mode],
            "confirmed": self.mode_confirmed,
            "period_id": self.mode_period_id,
            "session_start": self.session_start,
            "detected_if_type": self.net.if_type,
            "if_name": self.net.if_name,
            "mismatch": self._mode_mismatch(),
        }

    def _mode_mismatch(self) -> Optional[str]:
        t = self.net.if_type
        if t == "cabo" and self.mode in WIFI_MODES:
            return "O computador está usando uma conexão cabeada, mas o modo informado é Wi-Fi."
        if t == "wifi" and self.mode == "cabo":
            return "O computador está usando o Wi-Fi, mas o modo informado é cabo direto."
        return None

    # ---------------------------------------------------------- medição
    def _read_net(self) -> NetInfo:
        if self.simulated:
            return NetInfo(gateway="192.168.1.1", if_index=1,
                           if_type="wifi" if self.mode in WIFI_MODES else "cabo", if_name="Adaptador simulado")
        try:
            return self.netreader.read()
        except Exception as exc:
            log.warning("Falha ao ler o gateway: %s", exc)
            return NetInfo()

    def _wifi_relevant(self) -> bool:
        return self.mode in WIFI_MODES or self.net.if_type == "wifi"

    async def _probe(self, tid: str, host: Optional[str]) -> ProbeResult:
        t = self.s.target(tid)
        if host is None:
            return ProbeResult(None, M_FAIL, "sem gateway (sem rota de rede)")
        try:
            return await self.prober.probe(tid, host, t.tcp_port, self.s.timeout_ms)
        except Exception as exc:
            log.exception("Erro inesperado ao verificar %s", tid)
            return ProbeResult(None, M_FAIL, f"erro interno: {exc}")

    def _dish_should_probe(self, mono: float) -> bool:
        st = self.dish.status
        if st == "desativada":
            return False
        if st in ("detectando", "ativa"):
            return True
        return mono >= self.dish.next_retry

    def _dish_update(self, res: ProbeResult, ext_ok: bool, mono: float) -> ProbeResult:
        d = self.dish
        if d.status == "detectando":
            d.attempts += 1
            if res.ok:
                d.status = "ativa"
                log.info("%s acessível em %s: monitoramento ativado", self.s.target("dish").name,
                         self.s.target("dish").host)
                return res
            if d.attempts >= DISH_DETECT_ATTEMPTS:
                d.status = "indisponivel"
                d.next_retry = mono + self.s.dish_retry_minutes * 60
                log.info("%s não acessível pela rede; monitoramento desativado "
                         "(nova tentativa em %d min)", self.s.target("dish").name, self.s.dish_retry_minutes)
            return ProbeResult.not_probed()
        if d.status == "indisponivel":
            if res.ok:
                d.status, d.fail_streak = "ativa", 0
                log.info("%s voltou a ficar acessível", self.s.target("dish").name)
                return res
            d.next_retry = mono + self.s.dish_retry_minutes * 60
            return ProbeResult.not_probed()
        # ativa
        if res.ok:
            d.fail_streak = 0
        elif ext_ok:
            d.fail_streak += 1
            if d.fail_streak >= DISH_LOST_STREAK:
                d.status = "indisponivel"
                d.fail_streak = 0
                d.next_retry = mono + self.s.dish_retry_minutes * 60
                log.info("%s deixou de responder com a internet funcionando; "
                         "monitoramento do equipamento suspenso", self.s.target("dish").name)
        return res

    async def run_cycle(self, ts: int) -> dict:
        mono = time.monotonic()
        net = self._read_net()
        changed_net = (self.cycle_count > 0 and
                       (net.gateway != self.net.gateway or net.if_index != self.net.if_index))
        prev_net = self.net
        self.net = net

        gw_cfg = self.s.target("gw")
        gw_host = (net.gateway if gw_cfg.host == "auto" else gw_cfg.host)
        tasks: dict[str, Awaitable[ProbeResult]] = {}
        if gw_cfg.enabled:
            tasks["gw"] = self._probe("gw", gw_host)
        for tid in EXTERNAL_TARGETS:
            tc = self.s.target(tid)
            if tc.enabled:
                tasks[tid] = self._probe(tid, tc.host)
        probe_dish = self.s.target("dish").enabled and self._dish_should_probe(mono)
        if probe_dish:
            tasks["dish"] = self._probe("dish", self.s.target("dish").host)

        keys = list(tasks)
        values = await asyncio.gather(*(tasks[k] for k in keys))
        results: dict[str, ProbeResult] = {tid: ProbeResult.not_probed() for tid in TARGET_IDS}
        results.update(dict(zip(keys, values)))
        ext_ok = any(results[t].ok for t in EXTERNAL_TARGETS)
        if probe_dish:
            results["dish"] = self._dish_update(results["dish"], ext_ok, mono)

        wifi = self.wifi if self._wifi_relevant() else None
        cycle = Cycle(ts=ts, results=results, net=net, wifi=wifi, mode=self.mode,
                      mode_period_id=self.mode_period_id)
        out = self.detector.process(cycle)
        if out.wifi_refresh and self._wifi_relevant():
            self._wifi_kick.set()

        wifi_signal = wifi.signal_pct if (wifi and self.mode in WIFI_MODES) else None
        ts = self.db.insert_sample(ts, self.session_id, self.mode_period_id, out.sample_status,
                                   out.conn_status, out.primary_ms, results, out.ext_lost,
                                   out.ext_total, wifi_signal)
        event_payloads = self._persist_changes(out.changes)
        if changed_net:
            event_payloads.append(self._instant_event(E_NETCHANGE, ts, {
                "gateway_anterior": prev_net.gateway, "gateway_atual": net.gateway,
                "interface_anterior": prev_net.if_name, "interface_atual": net.if_name}))
            log.info("Rede alterada: gateway %s -> %s, interface %s -> %s",
                     prev_net.gateway, net.gateway, prev_net.if_name, net.if_name)
        self.db.commit()

        self.cycle_count += 1
        if out.conn_status != self.conn_status:
            self.dish_api.kick()   # durante quedas a antena é consultada com mais frequência
        self.conn_status = out.conn_status
        self.loss_pct = out.loss_pct
        self.lat_median = out.lat_median
        sample = {
            "ts": ts, "status": out.sample_status, "conn": out.conn_status, "lat": out.primary_ms,
            "lost": out.ext_lost, "total": out.ext_total, "wifi": wifi_signal,
            "mode_id": self.mode_period_id, "loss60": out.loss_pct, "median": out.lat_median,
            "fail_run": out.fail_run,
            "t": {tid: {"rtt": r.rtt_ms, "m": r.method, "err": r.error or None} for tid, r in results.items()},
        }
        self.last_cycle = sample
        self.live.append(sample)
        if len(self.live) > LIVE_BUFFER:
            del self.live[: len(self.live) - LIVE_BUFFER]

        await self.emit({"type": "sample", "sample": sample, "net": self.net_info(),
                         "dead": list(out.dead_targets)})
        for e in event_payloads:
            await self.emit({"type": "event", "event": e})
        return sample

    async def _handle_gap(self, last_ts: int, resume_ts: int, reason: str) -> None:
        log.warning("Monitoramento sem medições por %.1f s (%s)", (resume_ts - last_ts) / 1000, reason)
        payloads = self._persist_changes(self.detector.interrupt(last_ts, reason))
        payloads.append(self._instant_event(E_INACTIVE, last_ts, {"motivo": reason}, end_ts=resume_ts))
        self.db.commit()
        for p in payloads:
            await self.emit({"type": "event", "event": p})

    async def _cycle_loop(self) -> None:
        interval = self.s.interval_ms / 1000.0
        next_t = time.monotonic()
        prev_wall: Optional[float] = None
        prev_mono: Optional[float] = None
        while True:
            try:
                delay = next_t - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                wall = time.time()
                mono = time.monotonic()
                if prev_wall is not None and prev_mono is not None:
                    dwall = wall - prev_wall
                    dmono = mono - prev_mono
                    if dwall > self.s.gap_threshold_s:
                        reason = ("computador suspenso ou monitor sem executar" if dmono > self.s.gap_threshold_s
                                  else "computador suspenso ou relógio adiantado")
                        await self._handle_gap(int(prev_wall * 1000), int(wall * 1000), reason)
                    elif abs(dwall - dmono) > 2.0:
                        p = self._instant_event(E_CLOCK, int(wall * 1000), {
                            "diferenca_s": round(dwall - dmono, 1)})
                        self.db.commit()
                        await self.emit({"type": "event", "event": p})
                        log.warning("Relógio do sistema ajustado em %.1f s", dwall - dmono)
                prev_wall, prev_mono = wall, mono
                await self.run_cycle(int(wall * 1000))
                if (self.s.prevent_sleep and not self.simulated
                        and time.monotonic() - self._last_power_refresh > 300):
                    power.set_prevent_sleep(True)
                    self._last_power_refresh = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Erro no ciclo de medição (o monitoramento continua)")
                await asyncio.sleep(interval)
            interval = self.s.interval_ms / 1000.0
            next_t += interval
            now = time.monotonic()
            if next_t < now:   # ciclo atrasou: retoma no ritmo, sem rajadas
                next_t = now

    async def _wifi_loop(self) -> None:
        loop = asyncio.get_running_loop()
        rng = random.Random()
        while True:
            try:
                if self._wifi_relevant():
                    if self.simulated:
                        base = 70 + 10 * math.sin(time.time() / 60)
                        self.wifi = WifiInfo(available=True, connected=True, ssid="Rede simulada",
                                             interface="Wi-Fi (simulado)", signal_pct=int(base + rng.gauss(0, 4)))
                    else:
                        self.wifi = await loop.run_in_executor(None, read_wifi)
                else:
                    self.wifi = None
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Erro ao ler o sinal Wi-Fi")
            self._wifi_kick.clear()
            try:
                await asyncio.wait_for(self._wifi_kick.wait(), timeout=WIFI_REFRESH_S)
            except asyncio.TimeoutError:
                pass

    def refresh_stats(self) -> dict:
        self.stats = compute_stats(self.db, now_ms(), self.session_start)
        return self.stats

    async def _stats_loop(self) -> None:
        while True:
            try:
                await self.emit({"type": "stats", "stats": self.refresh_stats()})
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Erro ao calcular estatísticas")
            await asyncio.sleep(STATS_REFRESH_S)

    async def _maintenance_loop(self) -> None:
        last_retention = 0.0
        while True:
            try:
                await asyncio.sleep(60)
                self.db.aggregate_minutes(now_ms())
                if time.monotonic() - last_retention > 3600:
                    last_retention = time.monotonic()
                    day = 86400 * 1000
                    n = self.db.apply_retention(now_ms() - self.s.retention_raw_days * day,
                                                now_ms() - self.s.retention_agg_days * day)
                    if n:
                        log.info("Retenção: %d registro(s) antigo(s) removido(s)", n)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Erro na manutenção do banco")

    # ---------------------------------------------------------- consulta
    def net_info(self) -> dict:
        w = self.wifi
        return {
            "gateway": self.net.gateway,
            "if_type": self.net.if_type,
            "if_name": self.net.if_name,
            "wifi": None if w is None else {
                "available": w.available, "connected": w.connected, "signal": w.signal_pct,
                "rssi": w.rssi_dbm, "ssid": w.ssid, "error": w.error},
            "dish": self.dish.status,
            "mismatch": self._mode_mismatch(),
        }

    def targets_info(self) -> list[dict]:
        out = []
        for t in self.s.targets:
            host = t.host
            if t.id == "gw" and t.host == "auto":
                host = self.net.gateway or "não identificado"
            out.append({"id": t.id, "name": t.name, "host": host, "enabled": t.enabled,
                        "method": self.prober.method_info(t.id) if hasattr(self.prober, "method_info") else "ICMP",
                        "external": t.id in EXTERNAL_TARGETS})
        return out

    def _recent_samples(self, since: int) -> list[dict]:
        """Últimos minutos para o gráfico: memória + banco (após reiniciar o
        programa, o histórico recente vem do banco)."""
        live = [x for x in self.live if x["ts"] >= since]
        first_live = live[0]["ts"] if live else now_ms() + 1
        older = [x for x in self.db.samples_since(since, 2000) if x["ts"] < first_live]
        return older + live

    def _settings_public(self) -> dict:
        s = self.s
        return {
            "interval_ms": s.interval_ms, "timeout_ms": s.timeout_ms,
            "lat_normal_max_ms": s.lat_normal_max_ms, "lat_elevated_max_ms": s.lat_elevated_max_ms,
            "loss_threshold_pct": s.loss_threshold_pct, "loss_window_s": s.loss_window_s,
            "outage_confirm_cycles": s.outage_confirm_cycles, "chart_window_s": s.chart_window_s,
            "wifi_weak_signal_pct": s.wifi_weak_signal_pct, "prevent_sleep": s.prevent_sleep,
            "connection_modes": CONNECTION_MODES,
        }

    def snapshot(self) -> dict:
        s = self.s
        since = now_ms() - 30 * 60 * 1000   # o gráfico mantém 30 min em memória
        return {
            "version": __version__,
            "simulated": self.simulated,
            "server_time": now_ms(),
            "session_id": self.session_id,
            "conn_status": self.conn_status,
            "loss60": self.loss_pct,
            "median": self.lat_median,
            "settings": self._settings_public(),
            "provider": self.provider_info(),
            "mode": self.mode_info(),
            "net": self.net_info(),
            "targets": self.targets_info(),
            "samples": self._recent_samples(since),
            "bands": self.db.list_events(types=["interrupcao", "microqueda", "monitor_inativo"],
                                         since=since, limit=200, now_ms=now_ms()),
            "recent_failures": self.db.list_events(types=["interrupcao", "microqueda"], limit=40,
                                                   now_ms=now_ms()),
            "open_events": [self._event_payload(e) for e in self.detector.open_events() if e.id],
            "stats": self.stats or self.refresh_stats(),
            "dish_api": self.dish_api.public(),
            "conn_codes": CONN_CODE,
        }
