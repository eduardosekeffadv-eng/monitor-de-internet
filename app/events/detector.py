"""Detector de eventos: transforma os ciclos de medição em estados e ocorrências.

Regras (todas configuráveis):

* Ciclo "conectado": pelo menos um destino externo respondeu. A falha de
  um único servidor nunca é tratada como queda.
* Microqueda: todos os destinos externos sem resposta por 1 a N-1 ciclos.
* Interrupção confirmada: N ciclos seguidos (padrão 3) sem resposta de
  todos os destinos externos. O início é retroativo ao primeiro ciclo
  sem resposta e o horário carrega margem de ± 1 intervalo.
* Perda de pacotes: perda acima do limite (padrão 2 %) na janela móvel
  (padrão 60 s). Ciclos que fazem parte de uma interrupção confirmada não
  entram nessa conta (já estão contabilizados como interrupção).
* Latência elevada: mediana dos últimos ciclos acima do limite normal.

O detector é puro (não acessa banco nem rede), o que permite testá-lo
simulando qualquer sequência de quedas e restabelecimentos.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from ..config import EXTERNAL_TARGETS, WIFI_MODES, Settings
from ..monitor.types import (INSTAVEL, OFFLINE, ONLINE, S_CRITICAL, S_ELEVATED, S_FAIL,
                             S_OK, S_PARTIAL, Cycle)
from ..providers import profile
from .diagnosis import FailureStats, diagnose, diagnosis_title

E_OUTAGE = "interrupcao"
E_MICRO = "microqueda"
E_LOSS = "perda_pacotes"
E_LATENCY = "latencia_elevada"
E_INACTIVE = "monitor_inativo"
E_MODE = "modo_alterado"
E_START = "monitor_iniciado"
E_STOP = "monitor_encerrado"
E_NETCHANGE = "rede_alterada"
E_CLOCK = "ajuste_relogio"
E_PROVIDER = "tipo_conexao_alterado"
E_SETTINGS = "configuracoes_alteradas"
E_DISH_ALERT = "alerta_antena"

EVENT_LABEL = {
    E_OUTAGE: "Interrupção confirmada",
    E_MICRO: "Microqueda",
    E_LOSS: "Perda de pacotes",
    E_LATENCY: "Latência elevada",
    E_INACTIVE: "Monitoramento inativo",
    E_MODE: "Modo de conexão alterado",
    E_START: "Monitoramento iniciado",
    E_STOP: "Monitoramento encerrado",
    E_NETCHANGE: "Rede alterada",
    E_CLOCK: "Ajuste do relógio",
    E_PROVIDER: "Tipo de conexão alterado",
    E_SETTINGS: "Configurações alteradas",
    E_DISH_ALERT: "Alerta da antena",
}

LAT_AFTER_SAMPLES = 5
LAT_BEFORE_SAMPLES = 10
DEAD_TARGET_WINDOW = 60


@dataclass
class Event:
    type: str
    start_ts: int
    end_ts: Optional[int] = None
    precision_ms: int = 0
    mode: str = ""
    mode_period_id: Optional[int] = None
    wifi_signal: Optional[int] = None
    router_state: str = ""
    targets: dict = field(default_factory=dict)
    lat_before: Optional[float] = None
    lat_after: Optional[float] = None
    diagnosis: str = ""
    diagnosis_text: str = ""
    details: dict = field(default_factory=dict)
    truncated: bool = False
    id: Optional[int] = None

    @property
    def duration_ms(self) -> Optional[int]:
        return None if self.end_ts is None else max(0, self.end_ts - self.start_ts)


@dataclass
class CycleOutcome:
    sample_status: int
    conn_status: str
    primary_ms: Optional[float]
    ext_lost: int
    ext_total: int
    loss_pct: Optional[float]
    lat_median: Optional[float]
    fail_run: int
    changes: list = field(default_factory=list)     # [(acao, Event)]
    wifi_refresh: bool = False
    dead_targets: tuple = ()


@dataclass
class _WinEntry:
    ts: int
    lost: int
    total: int
    excluded: bool = False


class Detector:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.fail_run = 0
        self.first_fail_ts: Optional[int] = None
        self.run_stats = FailureStats()
        self.run_errors: dict[str, str] = {}
        self.outage: Optional[Event] = None
        self.loss_event: Optional[Event] = None
        self.lat_event: Optional[Event] = None
        self.window: deque[_WinEntry] = deque()
        self.lat_hist: deque[float] = deque(maxlen=settings.latency_window_cycles)
        self.recent_ok: deque[float] = deque(maxlen=LAT_BEFORE_SAMPLES)
        self.pending_after: list[tuple[Event, list[float]]] = []
        self.target_hist: dict[str, deque] = {t: deque(maxlen=DEAD_TARGET_WINDOW) for t in EXTERNAL_TARGETS}
        self.dead_targets: set[str] = set()
        self.last_ts: Optional[int] = None

    # ------------------------------------------------------------------ util
    def apply_settings(self, settings: Settings) -> None:
        self.s = settings
        self.lat_hist = deque(self.lat_hist, maxlen=settings.latency_window_cycles)

    @property
    def interval_ms(self) -> int:
        return self.s.interval_ms

    def _new_event(self, etype: str, start: int, cycle: Cycle) -> Event:
        return Event(type=etype, start_ts=start, precision_ms=self.interval_ms,
                     mode=cycle.mode, mode_period_id=cycle.mode_period_id)

    def _accumulate(self, cycle: Cycle) -> None:
        st = self.run_stats
        st.cycles += 1
        gw = cycle.results.get("gw")
        if cycle.net.gateway is None and gw is not None and gw.probed:
            st.no_route += 1
        if gw is not None and gw.probed:
            st.gw_probed += 1
            if not gw.ok:
                st.gw_fail += 1
        dish = cycle.results.get("dish")
        if dish is not None and dish.probed:
            st.dish_probed += 1
            if dish.ok:
                st.dish_ok += 1
        for tid, r in cycle.results.items():
            if not r.probed:
                continue
            st.target_probed[tid] = st.target_probed.get(tid, 0) + 1
            if not r.ok:
                st.target_fail[tid] = st.target_fail.get(tid, 0) + 1
                if r.error:
                    self.run_errors[tid] = r.error
        if cycle.wifi is not None and cycle.mode in WIFI_MODES and cycle.wifi.available:
            st.wifi_samples += 1
            if not cycle.wifi.connected:
                st.wifi_disconnected += 1
            elif cycle.wifi.signal_pct is not None:
                st.wifi_min_signal = (cycle.wifi.signal_pct if st.wifi_min_signal is None
                                      else min(st.wifi_min_signal, cycle.wifi.signal_pct))

    def _fill_failure(self, ev: Event, cycle: Cycle) -> None:
        st = self.run_stats
        prof = profile(self.s.provider_type)
        code, text = diagnose(st, cycle.mode, self.s.wifi_weak_signal_pct, prof)
        ev.diagnosis = code
        ev.diagnosis_text = text
        ev.router_state = st.router_state()
        ev.targets = st.target_states()
        ev.wifi_signal = st.wifi_min_signal
        ev.details.update({
            "ciclos_sem_resposta": st.cycles,
            "erros": dict(self.run_errors),
            "diagnostico_titulo": diagnosis_title(code, prof),
            "tipo_conexao": prof.label,
            "gateway": cycle.net.gateway,
            "interface": cycle.net.if_name,
        })
        if cycle.mode in WIFI_MODES and st.wifi_samples == 0:
            ev.details["wifi"] = "sinal indisponível"

    def _mean_recent(self) -> Optional[float]:
        return round(statistics.fmean(self.recent_ok), 1) if self.recent_ok else None

    def _finish_pending_after(self, changes: list, force: bool = False) -> None:
        keep = []
        for ev, vals in self.pending_after:
            if len(vals) >= LAT_AFTER_SAMPLES or (force and vals):
                ev.lat_after = round(statistics.fmean(vals), 1)
                changes.append(("update", ev))
            elif not force:
                keep.append((ev, vals))
        self.pending_after = keep

    def _close_instability(self, end_ts: int, changes: list, truncated: bool = False) -> None:
        for attr in ("loss_event", "lat_event"):
            ev = getattr(self, attr)
            if ev is not None:
                ev.end_ts = max(end_ts, ev.start_ts)
                ev.truncated = truncated
                changes.append(("close", ev))
                setattr(self, attr, None)

    # ------------------------------------------------------------ principal
    def process(self, cycle: Cycle) -> CycleOutcome:
        changes: list = []
        ts = cycle.ts
        self.last_ts = ts

        ext = {t: cycle.results[t] for t in EXTERNAL_TARGETS
               if t in cycle.results and cycle.results[t].probed}
        ext_ok = {t: r for t, r in ext.items() if r.ok}
        any_ok = bool(ext_ok)
        primary = min((r.rtt_ms for r in ext_ok.values()), default=None)

        # Saúde de cada destino externo (um servidor fora do ar não deve
        # manter a conexão "instável" para sempre).
        for t, r in ext.items():
            others_ok = any(o.ok for k, o in ext.items() if k != t)
            if others_ok or r.ok:
                self.target_hist[t].append(r.ok)
            h = self.target_hist[t]
            if r.ok:
                self.dead_targets.discard(t)
            elif len(h) == h.maxlen and not any(h):
                self.dead_targets.add(t)
        counted = {t: r for t, r in ext.items() if t not in self.dead_targets or r.ok}
        if not counted:
            counted = ext
        ext_total = len(counted)
        ext_lost = sum(1 for r in counted.values() if not r.ok)

        entry = _WinEntry(ts, ext_lost, ext_total)
        self.window.append(entry)
        horizon = ts - self.s.loss_window_s * 1000
        while self.window and self.window[0].ts < horizon:
            self.window.popleft()

        wifi_refresh = False
        if not any_ok:
            self.fail_run += 1
            if self.fail_run == 1:
                self.first_fail_ts = ts
                self.run_stats = FailureStats()
                self.run_errors = {}
                wifi_refresh = True
                self._finish_pending_after(changes, force=True)
            self._accumulate(cycle)
            if self.outage is None and self.fail_run >= self.s.outage_confirm_cycles:
                ev = self._new_event(E_OUTAGE, self.first_fail_ts or ts, cycle)
                ev.lat_before = self._mean_recent()
                self._fill_failure(ev, cycle)
                self.outage = ev
                changes.append(("open", ev))
                # Os ciclos desta falha passam a contar como interrupção, não como perda.
                for e in list(self.window)[-self.fail_run:]:
                    e.excluded = True
                self._close_instability(ev.start_ts, changes)
            elif self.outage is not None:
                entry.excluded = True
                prev_diag = self.outage.diagnosis
                self._fill_failure(self.outage, cycle)
                if self.outage.diagnosis != prev_diag:
                    changes.append(("update", self.outage))
        else:
            if self.outage is not None:
                ev = self.outage
                ev.end_ts = ts
                self._fill_failure(ev, cycle)
                changes.append(("close", ev))
                self.pending_after.append((ev, []))
                self.outage = None
            elif self.fail_run > 0:
                ev = self._new_event(E_MICRO, self.first_fail_ts or ts, cycle)
                ev.end_ts = ts
                ev.lat_before = self._mean_recent()
                self._fill_failure(ev, cycle)
                changes.append(("instant", ev))
                self.pending_after.append((ev, []))
            self.fail_run = 0
            self.first_fail_ts = None
            if primary is not None:
                self.lat_hist.append(primary)
                self.recent_ok.append(primary)
                for _ev, vals in self.pending_after:
                    vals.append(primary)
                self._finish_pending_after(changes)

        # ---- perda de pacotes na janela móvel
        considered = [e for e in self.window if not e.excluded]
        total = sum(e.total for e in considered)
        lost = sum(e.lost for e in considered)
        loss_pct = round(100.0 * lost / total, 2) if total else None
        lat_median = round(statistics.median(self.lat_hist), 1) if self.lat_hist else None

        if self.outage is None and self.fail_run == 0:
            enough = len(considered) >= self.s.loss_min_cycles
            if self.loss_event is None:
                if enough and loss_pct is not None and loss_pct > self.s.loss_threshold_pct:
                    ev = self._new_event(E_LOSS, ts, cycle)
                    ev.precision_ms = self.s.loss_window_s * 1000
                    ev.details = {"perda_max_pct": loss_pct, "janela_s": self.s.loss_window_s}
                    ev.lat_before = self._mean_recent()
                    self.loss_event = ev
                    changes.append(("open", ev))
            else:
                if loss_pct is not None:
                    self.loss_event.details["perda_max_pct"] = max(
                        self.loss_event.details.get("perda_max_pct", 0), loss_pct)
                if loss_pct is None or loss_pct <= self.s.loss_threshold_pct:
                    self.loss_event.end_ts = ts
                    changes.append(("close", self.loss_event))
                    self.loss_event = None

            min_lat = min(10, self.lat_hist.maxlen or 10)
            if self.lat_event is None:
                if (len(self.lat_hist) >= min_lat and lat_median is not None
                        and lat_median > self.s.lat_normal_max_ms):
                    ev = self._new_event(E_LATENCY, ts, cycle)
                    ev.precision_ms = self.interval_ms * len(self.lat_hist)
                    ev.details = {"mediana_max_ms": lat_median, "pico_ms": max(self.lat_hist)}
                    self.lat_event = ev
                    changes.append(("open", ev))
            else:
                d = self.lat_event.details
                if lat_median is not None:
                    d["mediana_max_ms"] = max(d.get("mediana_max_ms", 0), lat_median)
                if primary is not None:
                    d["pico_ms"] = max(d.get("pico_ms", 0), primary)
                if lat_median is None or lat_median <= self.s.lat_normal_max_ms * 0.9:
                    self.lat_event.end_ts = ts
                    changes.append(("close", self.lat_event))
                    self.lat_event = None

        # ---- status da amostra e da conexão
        if not any_ok:
            sample_status = S_FAIL
        elif ext_lost > 0:
            sample_status = S_PARTIAL
        elif primary is not None and primary > self.s.lat_elevated_max_ms:
            sample_status = S_CRITICAL
        elif primary is not None and primary > self.s.lat_normal_max_ms:
            sample_status = S_ELEVATED
        else:
            sample_status = S_OK

        if self.outage is not None:
            conn = OFFLINE
        elif self.fail_run > 0 or self.loss_event is not None or self.lat_event is not None:
            conn = INSTAVEL
        else:
            conn = ONLINE

        return CycleOutcome(sample_status, conn, primary, ext_lost, ext_total, loss_pct,
                            lat_median, self.fail_run, changes, wifi_refresh,
                            tuple(sorted(self.dead_targets)))

    # -------------------------------------------------- lacunas do monitor
    def interrupt(self, at_ts: int, reason: str) -> list:
        """Encerra o que estiver aberto quando o monitoramento é interrompido
        (computador suspenso, programa fechado). Nada é estendido para dentro
        do período sem medições: os eventos terminam na última medição."""
        changes: list = []
        if self.outage is not None:
            ev = self.outage
            ev.end_ts = max(at_ts, ev.start_ts)
            ev.truncated = True
            ev.details["observacao"] = (f"Encerrada sem confirmação de restabelecimento: {reason}. "
                                        "A duração vai até a última medição.")
            changes.append(("close", ev))
            self.outage = None
        self._close_instability(at_ts, changes, truncated=True)
        self._finish_pending_after(changes, force=True)
        self.fail_run = 0
        self.first_fail_ts = None
        self.window.clear()
        self.lat_hist.clear()
        self.recent_ok.clear()
        for h in self.target_hist.values():
            h.clear()
        self.dead_targets.clear()
        return changes

    def open_events(self) -> list[Event]:
        return [e for e in (self.outage, self.loss_event, self.lat_event) if e is not None]
