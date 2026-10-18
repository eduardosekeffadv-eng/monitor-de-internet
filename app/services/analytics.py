"""Análise de períodos: séries agregadas, estatísticas, mapa de calor, comparativos.

Base de cálculo
* Tempo monitorado: soma dos ciclos medidos × intervalo. Períodos sem
  medição (PC desligado/suspenso, programa fechado) não entram.
* Disponibilidade = (tempo monitorado − tempo em interrupção confirmada)
  ÷ tempo monitorado.
* Perda de pacotes: verificações externas sem resposta fora de interrupções
  confirmadas (as interrupções já são contadas como indisponibilidade).
* Para períodos longos, os dados vêm das médias por minuto (agg_minute),
  que são mantidas por mais tempo que os dados brutos.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from ..config import CONNECTION_MODES, Settings
from ..events.detector import E_INACTIVE, E_LATENCY, E_LOSS, E_MICRO, E_OUTAGE
from ..storage.db import Database
from .formatting import fmt_datetime, fmt_duration, fmt_num, fmt_pct, plural

MIN = 60_000
HOUR = 3_600_000
DAY = 86_400_000


def now_ms() -> int:
    return int(time.time() * 1000)


def tz_offset_ms(ts: int) -> int:
    off = datetime.fromtimestamp(ts / 1000).astimezone().utcoffset()
    return int(off.total_seconds() * 1000) if off else 0


def choose_bucket(span_ms: int) -> int:
    """Tamanho do intervalo de agregação para ~300 pontos no gráfico (0 = dados brutos)."""
    if span_ms <= 15 * MIN:
        return 0
    if span_ms <= HOUR:
        return 10_000
    if span_ms <= 6 * HOUR:
        return MIN
    if span_ms <= 24 * HOUR:
        return 5 * MIN
    if span_ms <= 7 * DAY:
        return 30 * MIN
    if span_ms <= 31 * DAY:
        return 2 * HOUR
    return DAY


def bucket_label(bucket_ms: int) -> str:
    if bucket_ms == 0:
        return "cada medição (1 por ciclo)"
    if bucket_ms < MIN:
        return f"médias a cada {bucket_ms // 1000} s"
    if bucket_ms < HOUR:
        return f"médias a cada {bucket_ms // MIN} min"
    if bucket_ms < DAY:
        h = bucket_ms // HOUR
        return "médias por hora" if h == 1 else f"médias a cada {h} h"
    return "médias por dia"


def _align(ts: int, bucket: int) -> int:
    """Alinha ao horário local (ex.: blocos de 2 h começando à meia-noite)."""
    off = tz_offset_ms(ts)
    return ts - ((ts + off) % bucket)


def _status(n: int, n_offline: int, n_fail: int, loss: Optional[float], lat: Optional[float],
            s: Settings) -> str:
    if n_offline:
        return "offline"
    if n_fail:
        return "falha"
    if loss is not None and loss > s.loss_threshold_pct:
        return "perda"
    if lat is not None and lat > s.lat_elevated_max_ms:
        return "critica"
    if lat is not None and lat > s.lat_normal_max_ms:
        return "elevada"
    return "ok"


def _row_covered(r) -> int:
    v = r["covered_ms"]
    return int(v) if v is not None else int(r["n"]) * 1000


# ------------------------------------------------------------------ séries
def series(db: Database, s: Settings, start: int, end: int) -> dict:
    bucket = choose_bucket(end - start)
    points: list[dict] = []
    if bucket == 0:
        for x in db.samples_between(start, end, 5000):
            points.append({"t": x["ts"], "lat": x["lat"], "status": x["status"], "conn": x["conn"],
                           "lost": x["lost"], "total": x["total"], "wifi": x["wifi"], "tg": x["t"]})
        return {"bucket_ms": 0, "kind": "raw", "label": bucket_label(0), "points": points,
                "start": start, "end": end, "interval_ms": s.interval_ms}

    if bucket < MIN:
        for r in db.raw_buckets(start, end, bucket):
            loss = 100.0 * r["lost_on"] / r["total_on"] if r["total_on"] else None
            lat = r["lat_avg"]
            points.append({"t": r["t"], "n": r["n"], "lat": _r(lat), "lat_min": _r(r["lat_min"]),
                           "lat_max": _r(r["lat_max"]), "loss": _r(loss, 2), "n_fail": r["n_fail"],
                           "n_offline": r["n_offline"], "covered_ms": r["n"] * s.interval_ms,
                           "status": _status(r["n"], r["n_offline"], r["n_fail"], loss, lat, s)})
    else:
        acc: dict[int, dict[str, Any]] = {}
        for r in db.minute_rows(start, end):
            k = _align(r["minute_ts"], bucket)
            a = acc.setdefault(k, {"n": 0, "n_lat": 0, "lat_sum": 0.0, "lat_min": None, "lat_max": None,
                                   "lost": 0, "total": 0, "n_fail": 0, "n_offline": 0, "covered": 0})
            a["n"] += r["n"]
            a["covered"] += _row_covered(r)
            if r["n_lat"]:
                a["n_lat"] += r["n_lat"]
                a["lat_sum"] += r["lat_avg"] * r["n_lat"]
                a["lat_min"] = r["lat_min"] if a["lat_min"] is None else min(a["lat_min"], r["lat_min"])
                a["lat_max"] = r["lat_max"] if a["lat_max"] is None else max(a["lat_max"], r["lat_max"])
            a["lost"] += r["lost_on"] or 0
            a["total"] += r["total_on"] or 0
            a["n_fail"] += r["n_fail"]
            a["n_offline"] += r["n_offline"]
        for k in sorted(acc):
            a = acc[k]
            lat = a["lat_sum"] / a["n_lat"] if a["n_lat"] else None
            loss = 100.0 * a["lost"] / a["total"] if a["total"] else None
            points.append({"t": k, "n": a["n"], "lat": _r(lat), "lat_min": _r(a["lat_min"]),
                           "lat_max": _r(a["lat_max"]), "loss": _r(loss, 2), "n_fail": a["n_fail"],
                           "n_offline": a["n_offline"], "covered_ms": a["covered"],
                           "status": _status(a["n"], a["n_offline"], a["n_fail"], loss, lat, s)})
    return {"bucket_ms": bucket, "kind": "agg", "label": bucket_label(bucket), "points": points,
            "start": start, "end": end, "interval_ms": s.interval_ms}


def _r(v: Optional[float], nd: int = 1) -> Optional[float]:
    return None if v is None else round(float(v), nd)


# -------------------------------------------------------------- estatística
def _clip(ev: dict, start: int, end: int, now: int) -> int:
    e = ev["end_ts"] if ev["end_ts"] is not None else now
    return max(0, min(e, end) - max(ev["start_ts"], start))


def _monitored(db: Database, start: int, end: int, rows: Optional[list] = None) -> int:
    if end - start <= DAY:
        exact = db.monitored_ms(start, end)
        if exact or rows is None:
            return exact
    rows = rows if rows is not None else db.minute_rows(start, end)
    return sum(_row_covered(r) for r in rows)


def rating(availability: Optional[float], outages_per_day: Optional[float], loss: Optional[float],
           micro_per_hour: Optional[float]) -> dict:
    """Classificação simples da estabilidade, com a explicação dos critérios."""
    if availability is None:
        return {"level": "sem_dados", "label": "Sem dados suficientes", "text": "O período não tem medições."}
    a, o, l, m = availability, outages_per_day or 0, loss or 0, micro_per_hour or 0
    if a >= 99.9 and o < 1 and l < 0.5 and m < 1:
        lv, lb = "excelente", "Excelente"
    elif a >= 99.5 and o < 4 and l < 1.5 and m < 4:
        lv, lb = "boa", "Boa"
    elif a >= 98 and o < 12 and l < 3:
        lv, lb = "regular", "Regular"
    else:
        lv, lb = "ruim", "Ruim"
    return {"level": lv, "label": lb,
            "text": ("Critérios: disponibilidade, interrupções por dia, perda de pacotes e microquedas por "
                     "hora monitorada. Excelente: ≥ 99,9%, < 1 interrupção/dia, perda < 0,5% e < 1 "
                     "microqueda/h · Boa: ≥ 99,5% · Regular: ≥ 98% · abaixo disso: Ruim.")}


def period_stats(db: Database, s: Settings, start: int, end: int, now: Optional[int] = None) -> dict:
    now = now or now_ms()
    end_eff = min(end, now)
    rows = db.minute_rows(start, end_eff)
    monitored = _monitored(db, start, end_eff, rows)

    outs = db.list_events(types=[E_OUTAGE], since=start, until=end_eff, limit=100000, now_ms=now)
    outs = [e for e in outs if (e["end_ts"] or now) > start]
    micros = db.list_events(types=[E_MICRO], since=start, until=end_eff, limit=100000, now_ms=now)
    micros = [e for e in micros if e["start_ts"] >= start]
    inst = db.list_events(types=[E_LOSS, E_LATENCY], since=start, until=end_eff, limit=100000, now_ms=now)
    inactive = db.list_events(types=[E_INACTIVE], since=start, until=end_eff, limit=100000, now_ms=now)

    out_clipped = [(e, _clip(e, start, end_eff, now)) for e in outs]
    outage_ms = sum(c for _, c in out_clipped)
    outage_count = sum(1 for e in outs if e["start_ts"] >= start)
    longest = max(out_clipped, key=lambda x: x[1], default=(None, 0))

    n_lat = sum(r["n_lat"] for r in rows)
    lat_avg = sum((r["lat_avg"] or 0) * r["n_lat"] for r in rows) / n_lat if n_lat else None
    lat_min = min((r["lat_min"] for r in rows if r["lat_min"] is not None), default=None)
    lat_max = max((r["lat_max"] for r in rows if r["lat_max"] is not None), default=None)
    lost = sum(r["lost_on"] or 0 for r in rows)
    total = sum(r["total_on"] or 0 for r in rows)
    loss = 100.0 * lost / total if total else None

    availability = max(0.0, 100.0 * (monitored - outage_ms) / monitored) if monitored else None
    days = monitored / DAY if monitored else 0
    hours = monitored / HOUR if monitored else 0
    per_day = outage_count / days if days >= 1 / 24 else None
    micro_per_hour = len(micros) / hours if hours >= 0.25 else None
    span = max(1, end_eff - start)

    return {
        "start": start, "end": end, "end_effective": end_eff,
        "monitored_ms": monitored,
        "coverage_pct": round(min(100.0, 100.0 * monitored / span), 2),
        "availability_pct": None if availability is None else round(availability, 3),
        "outage_count": outage_count,
        "outage_ms": outage_ms,
        "outage_avg_ms": int(outage_ms / len(outs)) if outs else None,
        "outage_longest": longest[0] and {**longest[0], "clipped_ms": longest[1]},
        "outages_per_day": None if per_day is None else round(per_day, 2),
        "micro_count": len(micros),
        "micro_ms": sum(e["duration_ms"] or 0 for e in micros),
        "micro_per_hour": None if micro_per_hour is None else round(micro_per_hour, 2),
        "instability_count": len(inst),
        "instability_ms": sum(_clip(e, start, end_eff, now) for e in inst),
        "latency_events": sum(1 for e in inst if e["type"] == E_LATENCY),
        "loss_events": sum(1 for e in inst if e["type"] == E_LOSS),
        "inactive_count": len(inactive),
        "lat_avg": _r(lat_avg), "lat_min": _r(lat_min), "lat_max": _r(lat_max),
        "loss_pct": _r(loss, 3),
        "rating": rating(availability, per_day, loss, micro_per_hour),
    }


# ---------------------------------------------------------- comparativos
def _mode_map(db: Database, start: int, end: int) -> dict[int, str]:
    return {p["id"]: p["mode"] for p in db.mode_periods_between(start - 7 * DAY, end + DAY)}


def by_mode(db: Database, s: Settings, start: int, end: int, now: Optional[int] = None) -> list[dict]:
    now = now or now_ms()
    end_eff = min(end, now)
    modes = _mode_map(db, start, end_eff)
    acc: dict[str, dict] = defaultdict(lambda: {"covered": 0, "n_lat": 0, "lat_sum": 0.0, "lost": 0,
                                                "total": 0, "outages": 0, "outage_ms": 0, "micro": 0})
    for r in db.minute_rows(start, end_eff):
        m = modes.get(r["mode_id"], "desconhecido")
        a = acc[m]
        a["covered"] += _row_covered(r)
        if r["n_lat"]:
            a["n_lat"] += r["n_lat"]
            a["lat_sum"] += r["lat_avg"] * r["n_lat"]
        a["lost"] += r["lost_on"] or 0
        a["total"] += r["total_on"] or 0
    for e in db.list_events(types=[E_OUTAGE, E_MICRO], since=start, until=end_eff, limit=100000, now_ms=now):
        m = e["mode"] or "desconhecido"
        a = acc[m]
        if e["type"] == E_OUTAGE:
            if e["start_ts"] >= start:
                a["outages"] += 1
            a["outage_ms"] += _clip(e, start, end_eff, now)
        elif e["start_ts"] >= start:
            a["micro"] += 1
    out = []
    for mode, a in acc.items():
        if not a["covered"] and not a["outages"]:
            continue
        avail = 100.0 * (a["covered"] - a["outage_ms"]) / a["covered"] if a["covered"] else None
        days = a["covered"] / DAY
        out.append({
            "mode": mode, "label": CONNECTION_MODES.get(mode, "Não identificado"),
            "monitored_ms": a["covered"],
            "availability_pct": None if avail is None else round(max(0.0, avail), 3),
            "outages": a["outages"], "outage_ms": a["outage_ms"], "micro": a["micro"],
            "outages_per_day": round(a["outages"] / days, 2) if days >= 1 / 24 else None,
            "lat_avg": _r(a["lat_sum"] / a["n_lat"]) if a["n_lat"] else None,
            "loss_pct": _r(100.0 * a["lost"] / a["total"], 3) if a["total"] else None,
        })
    out.sort(key=lambda x: -x["monitored_ms"])
    return out


def _day_key(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")


def _split_by_day(start: int, end: int) -> Iterable[tuple[str, int, int]]:
    """Divide [start, end) em pedaços por dia local."""
    cur = start
    while cur < end:
        d = datetime.fromtimestamp(cur / 1000)
        nxt = int((datetime(d.year, d.month, d.day) + timedelta(days=1)).timestamp() * 1000)
        piece_end = min(end, nxt)
        yield _day_key(cur), cur, piece_end
        cur = piece_end


def by_day(db: Database, s: Settings, start: int, end: int, now: Optional[int] = None) -> list[dict]:
    now = now or now_ms()
    end_eff = min(end, now)
    acc: dict[str, dict] = {}

    def slot(k: str) -> dict:
        return acc.setdefault(k, {"day": k, "covered": 0, "n_lat": 0, "lat_sum": 0.0, "lat_max": None,
                                  "lost": 0, "total": 0, "outages": 0, "outage_ms": 0, "micro": 0})

    for r in db.minute_rows(start, end_eff):
        a = slot(_day_key(r["minute_ts"]))
        a["covered"] += _row_covered(r)
        if r["n_lat"]:
            a["n_lat"] += r["n_lat"]
            a["lat_sum"] += r["lat_avg"] * r["n_lat"]
            a["lat_max"] = r["lat_max"] if a["lat_max"] is None else max(a["lat_max"], r["lat_max"])
        a["lost"] += r["lost_on"] or 0
        a["total"] += r["total_on"] or 0
    for e in db.list_events(types=[E_OUTAGE, E_MICRO], since=start, until=end_eff, limit=100000, now_ms=now):
        if e["type"] == E_OUTAGE:
            if e["start_ts"] >= start:
                slot(_day_key(e["start_ts"]))["outages"] += 1
            e_end = e["end_ts"] if e["end_ts"] is not None else now
            for k, a0, b0 in _split_by_day(max(e["start_ts"], start), min(e_end, end_eff)):
                slot(k)["outage_ms"] += max(0, b0 - a0)
        elif e["start_ts"] >= start:
            slot(_day_key(e["start_ts"]))["micro"] += 1
    out = []
    for k in sorted(acc):
        a = acc[k]
        avail = 100.0 * (a["covered"] - a["outage_ms"]) / a["covered"] if a["covered"] else None
        d = datetime.strptime(k, "%Y-%m-%d")
        out.append({
            "day": k, "ts": int(d.timestamp() * 1000), "weekday": d.weekday(),
            "monitored_ms": a["covered"],
            "availability_pct": None if avail is None else round(max(0.0, avail), 3),
            "outages": a["outages"], "outage_ms": a["outage_ms"], "micro": a["micro"],
            "lat_avg": _r(a["lat_sum"] / a["n_lat"]) if a["n_lat"] else None,
            "lat_max": _r(a["lat_max"]),
            "loss_pct": _r(100.0 * a["lost"] / a["total"], 3) if a["total"] else None,
        })
    return out


def heatmap(db: Database, s: Settings, start: int, end: int, now: Optional[int] = None) -> dict:
    """Problemas por dia × hora do dia, e o perfil médio por hora (todos os dias juntos)."""
    now = now or now_ms()
    end_eff = min(end, now)
    cells: dict[tuple[str, int], dict] = {}
    hours: list[dict] = [{"hour": h, "covered": 0, "offline": 0, "lost": 0, "total": 0, "n_lat": 0,
                          "lat_sum": 0.0, "outages": 0, "micro": 0} for h in range(24)]

    def cell(k: str, h: int) -> dict:
        return cells.setdefault((k, h), {"covered": 0, "offline": 0, "lost": 0, "total": 0, "n_lat": 0,
                                         "lat_sum": 0.0, "outages": 0, "micro": 0})

    for r in db.minute_rows(start, end_eff):
        dt = datetime.fromtimestamp(r["minute_ts"] / 1000)
        k, h = dt.strftime("%Y-%m-%d"), dt.hour
        cov = _row_covered(r)
        off = int(cov * r["n_offline"] / r["n"]) if r["n"] else 0
        for a in (cell(k, h), hours[h]):
            a["covered"] += cov
            a["offline"] += off
            a["lost"] += r["lost_on"] or 0
            a["total"] += r["total_on"] or 0
            if r["n_lat"]:
                a["n_lat"] += r["n_lat"]
                a["lat_sum"] += r["lat_avg"] * r["n_lat"]
    for e in db.list_events(types=[E_OUTAGE, E_MICRO], since=start, until=end_eff, limit=100000, now_ms=now):
        if e["start_ts"] < start:
            continue
        dt = datetime.fromtimestamp(e["start_ts"] / 1000)
        key = "outages" if e["type"] == E_OUTAGE else "micro"
        cell(dt.strftime("%Y-%m-%d"), dt.hour)[key] += 1
        hours[dt.hour][key] += 1

    def finish(a: dict) -> dict:
        return {
            "monitored_ms": a["covered"],
            "offline_ms": a["offline"],
            "availability_pct": (round(100.0 * (a["covered"] - a["offline"]) / a["covered"], 3)
                                 if a["covered"] else None),
            "loss_pct": _r(100.0 * a["lost"] / a["total"], 2) if a["total"] else None,
            "lat_avg": _r(a["lat_sum"] / a["n_lat"]) if a["n_lat"] else None,
            "outages": a["outages"], "micro": a["micro"],
        }

    days = sorted({k for k, _ in cells})
    grid = [{"day": k, "ts": int(datetime.strptime(k, "%Y-%m-%d").timestamp() * 1000),
             "hours": [finish(cells[(k, h)]) if (k, h) in cells else None for h in range(24)]} for k in days]
    profile = [dict(hour=h["hour"], **finish(h)) for h in hours]

    # horários com mais problemas (perfil por hora, com pelo menos 10 min monitorados)
    scored = []
    for p in profile:
        if p["monitored_ms"] < 10 * MIN:
            continue
        hours_mon = p["monitored_ms"] / HOUR
        score = (p["offline_ms"] / 1000) / hours_mon * 2 + (p["outages"] + p["micro"] * 0.3) / hours_mon * 30 \
            + (p["loss_pct"] or 0) * 5
        scored.append((score, p["hour"]))
    worst = [h for sc, h in sorted(scored, reverse=True)[:3] if sc > 0]
    return {"days": grid, "profile": profile, "worst_hours": worst}


# ---------------------------------------------------------- antena
def dish_analysis(db: Database, start: int, end: int, now: Optional[int] = None) -> Optional[dict]:
    """Quedas e estatísticas informadas pela própria antena Starlink no período."""
    now = now or now_ms()
    end_eff = min(end, now)
    summary = db.dish_cause_summary(start, end_eff)
    stats = db.dish_stats_summary(start, end_eff)
    if not summary["count"] and not stats:
        return None
    fails = [e for e in db.list_events(types=[E_OUTAGE, E_MICRO], since=start, until=end_eff, limit=100000,
                                       now_ms=now) if e["start_ts"] >= start]
    matched = [e for e in fails if e.get("dish")]
    seen = set()
    for e in matched:
        seen.add(e["dish"]["start_ts"])
    tol = 5000
    only_dish = [o for o in summary["outages"]
                 if not any(f["start_ts"] - tol < o["end_ts"] and (f["end_ts"] or now) + tol > o["start_ts"]
                            for f in fails)]
    return {"summary": summary, "stats": stats, "monitor_failures": len(fails), "matched": len(matched),
            "only_dish": len(only_dish)}


def dish_sentence(dish: Optional[dict]) -> str:
    if not dish or not dish["summary"]["count"]:
        return ""
    sm = dish["summary"]
    top = sm["causes"][0]
    txt = (f"Segundo a própria antena Starlink, houve {plural(sm['count'], 'queda registrada', 'quedas registradas')} "
           f"no período, somando {fmt_duration(sm['total_ms'])}; motivo mais frequente: {top['label'].lower()} "
           f"({top['count']} de {sm['count']}).")
    st = sm["sides"].get("starlink", 0)
    lo = sm["sides"].get("local", 0)
    if st or lo:
        txt += (f" Pelos motivos informados, {plural(st, 'queda foi', 'quedas foram')} da rede da Starlink"
                f" e {lo} {'foi ligada' if lo == 1 else 'foram ligadas'} à instalação ou à própria antena.")
    return txt


# ---------------------------------------------------------------- resumo
def summary_text(st: dict, outages: list[dict], dish: Optional[dict] = None) -> str:
    if not st["monitored_ms"]:
        return "Não há medições registradas neste período."
    parts = [
        f"A conexão foi monitorada por {fmt_duration(st['monitored_ms'])} "
        f"({fmt_num(st['coverage_pct'], 1)}% do período)."
    ]
    if st["outage_count"]:
        parts.append(
            f"Foram registradas {plural(st['outage_count'], 'interrupção confirmada', 'interrupções confirmadas')}, "
            f"somando {fmt_duration(st['outage_ms'])} sem conectividade externa"
            + (f"; a maior durou {fmt_duration(st['outage_longest']['clipped_ms'])}"
               f" ({fmt_datetime(st['outage_longest']['start_ts'], True)})" if st["outage_longest"] else "")
            + ".")
    else:
        parts.append("Não houve interrupções confirmadas.")
    if st["micro_count"]:
        parts.append(f"Também ocorreram {plural(st['micro_count'], 'microqueda', 'microquedas')} "
                     f"(falhas de até 2 s, abaixo do critério de interrupção).")
    parts.append(f"A disponibilidade medida foi de {fmt_pct(st['availability_pct'], 3)}, com latência média de "
                 f"{fmt_num(st['lat_avg'])} ms e perda de pacotes de {fmt_pct(st['loss_pct'], 2)}.")
    if outages:
        diag = defaultdict(int)
        for e in outages:
            diag[e["diagnosis_label"] or "Causa indeterminada"] += 1
        top, n = max(diag.items(), key=lambda x: x[1])
        parts.append(f"Diagnóstico mais frequente das interrupções: {top.lower()} ({n} de {len(outages)}).")
    ds = dish_sentence(dish)
    if ds:
        parts.append(ds)
    return " ".join(parts)


def analysis(db: Database, s: Settings, start: int, end: int) -> dict:
    now = now_ms()
    st = period_stats(db, s, start, end, now)
    return {
        "start": start, "end": end, "now": now,
        "stats": st,
        "series": series(db, s, start, min(end, now)),
        "by_mode": by_mode(db, s, start, end, now),
        "by_day": by_day(db, s, start, end, now),
        "heatmap": heatmap(db, s, start, end, now),
        "bands": db.list_events(types=[E_OUTAGE, E_MICRO, E_INACTIVE], since=start, until=min(end, now),
                                limit=5000, now_ms=now),
        "thresholds": {"normal": s.lat_normal_max_ms, "elevated": s.lat_elevated_max_ms,
                       "loss": s.loss_threshold_pct},
        "first_data_ts": db.first_sample_ts(),
        "dish": dish_analysis(db, start, end, now),
    }
