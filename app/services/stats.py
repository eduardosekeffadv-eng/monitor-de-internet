"""Indicadores do painel, calculados a partir do banco.

"Hoje" é o dia corrente no horário do computador (a partir de 00:00).
Disponibilidade = (tempo monitorado − tempo em interrupção confirmada) ÷
tempo monitorado. Momentos em que o monitor não estava medindo não entram
na conta.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from ..events.detector import E_INACTIVE, E_MICRO, E_OUTAGE
from ..storage.db import Database


def local_midnight_ms(now_ms: Optional[int] = None) -> int:
    now = datetime.fromtimestamp((now_ms or int(time.time() * 1000)) / 1000).astimezone()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp() * 1000)


def compute_stats(db: Database, now_ms: int, session_start: int) -> dict:
    since = local_midnight_ms(now_ms)
    until = now_ms + 1
    s = db.sample_stats(since, until)
    outages, outage_ms = db.outage_time(E_OUTAGE, since, until, now_ms)
    micros, micro_ms = db.outage_time(E_MICRO, since, until, now_ms)
    monitored = db.monitored_ms(since, until)

    availability = None
    if monitored > 0:
        availability = max(0.0, min(100.0, 100.0 * (monitored - outage_ms) / monitored))

    loss = None
    if s["total"]:
        loss = round(100.0 * (s["lost"] or 0) / s["total"], 2)

    last_outage = db.last_event(E_OUTAGE)
    last_inactive = db.last_event(E_INACTIVE)

    # Conta-se a partir do evento mais recente entre: início desta sessão,
    # retomada após o monitor ficar inativo e fim da última interrupção.
    # Não é possível afirmar que a conexão esteve online enquanto não se media.
    candidates = [(session_start, "inicio")]
    if last_inactive and last_inactive["end_ts"]:
        candidates.append((last_inactive["end_ts"], "retomada"))
    if last_outage and last_outage["end_ts"]:
        candidates.append((last_outage["end_ts"], "ultima_interrupcao"))
    online_since, online_kind = max(candidates, key=lambda c: c[0])
    if last_outage and last_outage["open"]:
        online_since, online_kind = None, "offline"

    return {
        "now": now_ms,
        "period_start": since,
        "samples_today": s["n"],
        "lat_avg": round(s["lat_avg"], 1) if s["lat_avg"] is not None else None,
        "lat_min": s["lat_min"],
        "lat_max": s["lat_max"],
        "loss_today_pct": loss,
        "outages_today": outages,
        "outage_ms_today": outage_ms,
        "micro_today": micros,
        "micro_ms_today": micro_ms,
        "monitored_ms_today": monitored,
        "availability_pct": round(availability, 3) if availability is not None else None,
        "last_outage": last_outage,
        "online_since": online_since,
        "online_since_kind": online_kind,
        "db_bytes": db.size_bytes(),
    }
