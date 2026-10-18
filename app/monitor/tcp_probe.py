"""Teste TCP alternativo: mede o tempo de abertura de uma conexão.

Usado quando um destino não responde ao ICMP. Apenas o handshake é feito
(nenhum dado é enviado), então o consumo de banda é desprezível.
Uma recusa imediata da conexão (RST) também prova que o destino está
alcançável e respondendo, por isso conta como resposta.
"""

from __future__ import annotations

import asyncio
import time

from .types import M_FAIL, M_TCP, ProbeResult


async def tcp_probe(host: str, port: int, timeout_ms: int) -> ProbeResult:
    start = time.perf_counter()
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout_ms / 1000.0
        )
    except ConnectionRefusedError:
        elapsed = (time.perf_counter() - start) * 1000.0
        if elapsed <= timeout_ms:
            return ProbeResult(round(elapsed, 1), M_TCP, "porta fechada (host respondeu)")
        return ProbeResult(None, M_FAIL, "recusado")
    except asyncio.TimeoutError:
        return ProbeResult(None, M_FAIL, "tempo esgotado (TCP)")
    except OSError as exc:
        return ProbeResult(None, M_FAIL, f"TCP: {exc.strerror or exc}")
    elapsed = (time.perf_counter() - start) * 1000.0
    try:
        writer.close()
    except Exception:
        pass
    return ProbeResult(round(elapsed, 1), M_TCP)
