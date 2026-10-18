"""Distribuição das mensagens em tempo real para as abas do painel (WebSocket).

Cada aba conectada tem uma fila própria. Se uma aba ficar lenta demais
(fila cheia), ela é desconectada e o próprio painel reconecta e recarrega
o estado — o monitoramento nunca espera pelo navegador.
"""

from __future__ import annotations

import asyncio
import json
import logging

log = logging.getLogger(__name__)


class Hub:
    def __init__(self, max_queue: int = 300) -> None:
        self._clients: set[asyncio.Queue] = set()
        self._max = max_queue

    @property
    def count(self) -> int:
        return len(self._clients)

    def register(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max)
        self._clients.add(q)
        return q

    def unregister(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    def broadcast(self, message: dict) -> None:
        if not self._clients:
            return
        data = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        for q in list(self._clients):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                log.info("Aba do painel lenta demais; desconectando para ressincronizar")
                self._clients.discard(q)
                while not q.empty():
                    q.get_nowait()
                q.put_nowait(None)   # sinaliza para a conexão encerrar
