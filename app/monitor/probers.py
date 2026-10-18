"""Estratégia de verificação de cada destino (ICMP com alternativa TCP).

Por que não "ICMP e, se falhar, TCP" em sequência? Com timeout de 800 ms
e um ciclo de 1 s, um ICMP sem resposta já consome quase todo o ciclo, e o
TCP em seguida atrasaria a próxima medição justamente durante uma queda.
A estratégia adotada:

* Normal: só ICMP.
* Se o ICMP de um destino falhar, ele fica "suspeito": nos ciclos
  seguintes ICMP e TCP são disparados EM PARALELO, e qualquer resposta vale.
  Como a interrupção só é confirmada após 3 ciclos, as confirmações sempre
  contam com o teste TCP — evitando falsos positivos.
* Se o destino não responde a ICMP mas responde a TCP por 3 ciclos
  seguidos, ele passa a usar TCP como método principal, e o ICMP é
  retestado a cada 60 s.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

from .tcp_probe import tcp_probe
from .types import M_FAIL, M_ICMP, M_TCP, ProbeResult

ICMP_RECHECK_S = 60.0
TCP_SWITCH_STREAK = 3


@dataclass
class _TargetState:
    suspect: bool = False
    icmp_fail_streak: int = 0
    tcp_mode: bool = False
    last_icmp_recheck: float = 0.0


class RealProber:
    """Verificações reais (ICMP nativo do Windows + TCP)."""

    simulated = False

    def __init__(self, icmp_ping: Optional[Callable[[str, int], ProbeResult]] = None,
                 tcp: Optional[Callable] = None) -> None:
        if icmp_ping is None:
            from . import icmp_windows  # só importável no Windows
            icmp_ping = icmp_windows.ping
        self._icmp = icmp_ping
        self._tcp = tcp or tcp_probe
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="icmp")
        self._state: dict[str, _TargetState] = {}

    def method_info(self, tid: str) -> str:
        st = self._state.get(tid)
        if st is None:
            return "ICMP"
        if st.tcp_mode:
            return "TCP (o destino não responde a ICMP)"
        return "ICMP + TCP (verificação reforçada)" if st.suspect else "ICMP"

    async def _icmp_async(self, host: str, timeout_ms: int) -> ProbeResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, self._icmp, host, timeout_ms)

    async def probe(self, tid: str, host: str, tcp_port: int, timeout_ms: int) -> ProbeResult:
        st = self._state.setdefault(tid, _TargetState())
        now = time.monotonic()
        run_icmp = True
        run_tcp = st.suspect or st.tcp_mode
        if st.tcp_mode and now - st.last_icmp_recheck < ICMP_RECHECK_S:
            run_icmp = False

        icmp_res: Optional[ProbeResult] = None
        tcp_res: Optional[ProbeResult] = None
        if run_icmp and run_tcp:
            icmp_res, tcp_res = await asyncio.gather(
                self._icmp_async(host, timeout_ms), self._tcp(host, tcp_port, timeout_ms))
        elif run_icmp:
            icmp_res = await self._icmp_async(host, timeout_ms)
        else:
            tcp_res = await self._tcp(host, tcp_port, timeout_ms)

        if icmp_res is not None:
            if st.tcp_mode:
                st.last_icmp_recheck = now
            if icmp_res.ok:
                st.suspect = False
                st.icmp_fail_streak = 0
                st.tcp_mode = False
            else:
                st.suspect = True
                st.icmp_fail_streak += 1
                if tcp_res is not None and tcp_res.ok and st.icmp_fail_streak >= TCP_SWITCH_STREAK:
                    st.tcp_mode = True

        if icmp_res is not None and icmp_res.ok:
            return icmp_res
        if tcp_res is not None and tcp_res.ok:
            return tcp_res
        errors = [r.error for r in (icmp_res, tcp_res) if r is not None and r.error]
        return ProbeResult(None, M_FAIL, "; ".join(errors) or "sem resposta")

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


class SimulatedProber:
    """Gerador de dados FICTÍCIOS, usado apenas no modo simulação.

    Serve para demonstrar e testar o painel fora do Windows. Quando ativo,
    o painel exibe uma faixa "MODO SIMULAÇÃO" e os dados vão para um banco
    separado (data/simulacao.db).
    """

    simulated = True

    def __init__(self, seed: Optional[int] = None) -> None:
        self._rng = random.Random(seed)
        self._t0 = time.time()
        self._cycle_key: Optional[int] = None
        self._scenario: dict[str, bool] = {}
        self._base = 38.0

    def method_info(self, tid: str) -> str:
        return "simulado"

    def _scenario_for(self, now: float) -> dict[str, bool]:
        key = int(now)
        if key != self._cycle_key:
            self._cycle_key = key
            t = now - self._t0
            outage = 40 <= (t % 300) < 49           # queda de ~9 s a cada 5 min
            local = outage and int(t // 300) % 3 == 2   # 1 em cada 3 com o roteador fora
            micro = (t % 90) >= 88                   # microqueda de ~2 s a cada 1,5 min
            spike = 150 <= (t % 200) < 170           # 20 s de latência alta
            self._scenario = {"outage": outage or micro, "local": local, "spike": spike}
        return self._scenario

    async def probe(self, tid: str, host: str, tcp_port: int, timeout_ms: int) -> ProbeResult:
        now = time.time()
        sc = self._scenario_for(now)
        rng = self._rng
        if tid == "gw":
            if sc["local"]:
                return ProbeResult(None, M_FAIL, "tempo esgotado (simulado)")
            return ProbeResult(float(rng.choice([0, 0, 1, 1, 1, 2])), M_ICMP)
        if tid == "dish":
            if sc["local"]:
                return ProbeResult(None, M_FAIL, "tempo esgotado (simulado)")
            return ProbeResult(float(rng.choice([0, 1, 1])), M_ICMP)
        if sc["outage"]:
            return ProbeResult(None, M_FAIL, "tempo esgotado (simulado)")
        if rng.random() < 0.006:
            return ProbeResult(None, M_FAIL, "tempo esgotado (simulado)")
        wave = 6 * math.sin(now / 9.0) + 4 * math.sin(now / 2.3)
        lat = self._base + wave + rng.gauss(0, 5)
        if sc["spike"]:
            lat += 90 + rng.random() * 160
        elif rng.random() < 0.02:
            lat += rng.random() * 140
        if tid == "gg":
            lat += 4
        await asyncio.sleep(min(lat, timeout_ms) / 1000.0 * 0.2)
        return ProbeResult(round(max(8.0, lat)), M_ICMP if tid != "gg" or rng.random() > 0.05 else M_TCP)

    def close(self) -> None:
        pass
