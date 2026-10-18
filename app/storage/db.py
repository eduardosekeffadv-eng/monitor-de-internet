"""Acesso ao SQLite: gravação das medições e eventos, consultas e manutenção.

* Modo WAL com synchronous=NORMAL: cada ciclo é confirmado (commit) logo
  após ser medido. Se o programa for encerrado de forma inesperada, perde-se
  no máximo o ciclo em andamento; o banco nunca fica corrompido.
* Todas as operações acontecem na thread do servidor (o volume é pequeno:
  uma linha por segundo), protegidas por um lock por segurança.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from ..config import CONNECTION_MODES
from ..events.detector import EVENT_LABEL, Event
from ..events.diagnosis import DIAGNOSIS_LABEL
from ..monitor.dish_api import cause_info, cause_key
from ..monitor.types import INSTAVEL, OFFLINE, ONLINE, ProbeResult

log = logging.getLogger(__name__)

SCHEMA_VERSION = "4"
CONN_CODE = {ONLINE: 0, INSTAVEL: 1, OFFLINE: 2}
CONN_NAME = {v: k for k, v in CONN_CODE.items()}
TARGET_COLS = ("gw", "cf", "gg", "dish")
FAILURE_TYPES = ("interrupcao", "microqueda")
# Tolerância ao comparar o horário da antena com o do computador.
DISH_MATCH_TOL_MS = 5000

# Colunas agregadas por minuto (usadas tanto para gravar agg_minute quanto
# para calcular, na hora, os minutos mais recentes ainda não agregados).
AGG_COLS = ("minute_ts, mode_id, n, n_lat, lat_avg, lat_min, lat_max, ext_lost, ext_total, n_fail,"
            " n_offline, gw_avg, wifi_avg, covered_ms, lost_on, total_on")
AGG_SELECT = (
    "SELECT (sa.ts/60000)*60000 AS minute_ts, MAX(sa.mode_id) AS mode_id, COUNT(*) AS n, COUNT(sa.lat) AS n_lat,"
    " AVG(sa.lat) AS lat_avg, MIN(sa.lat) AS lat_min, MAX(sa.lat) AS lat_max, SUM(sa.ext_lost) AS ext_lost,"
    " SUM(sa.ext_total) AS ext_total, SUM(sa.status=4) AS n_fail, SUM(sa.conn=2) AS n_offline,"
    " AVG(sa.gw) AS gw_avg, AVG(sa.wifi) AS wifi_avg, SUM(se.interval_ms) AS covered_ms,"
    " SUM(CASE WHEN sa.conn<2 THEN sa.ext_lost ELSE 0 END) AS lost_on,"
    " SUM(CASE WHEN sa.conn<2 THEN sa.ext_total ELSE 0 END) AS total_on"
    " FROM samples sa JOIN sessions se ON se.id = sa.session_id"
    " WHERE sa.ts>=? AND sa.ts<? GROUP BY sa.ts/60000")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.path.exists()
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        if is_new:
            # precisa vir antes de qualquer tabela
            self.conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute("PRAGMA cache_size=-8000")
        schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
        self.conn.executescript(schema)
        self._migrate()
        self.set_meta("schema_version", SCHEMA_VERSION)
        self.conn.commit()
        self._last_sample_ts = self.last_sample_ts() or 0

    def _migrate(self) -> None:
        """Atualiza bancos criados por versões anteriores, sem perder dados."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(sessions)")}
        if "provider" not in cols:
            self.conn.execute("ALTER TABLE sessions ADD COLUMN provider TEXT")
            # A versão 1.x (Starlink Monitor) monitorava apenas Starlink.
            self.conn.execute("UPDATE sessions SET provider='starlink' WHERE provider IS NULL")
            log.info("Banco de dados atualizado para o esquema 2")
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(agg_minute)")}
        if "covered_ms" not in cols:
            for c in ("covered_ms", "lost_on", "total_on"):
                self.conn.execute(f"ALTER TABLE agg_minute ADD COLUMN {c} INTEGER")
            # Minutos antigos sem dados brutos: estimativa a partir do que existe.
            self.conn.execute("UPDATE agg_minute SET covered_ms = n*1000, lost_on = ext_lost,"
                              " total_on = ext_total")
            # Onde ainda há dados brutos, reagrega com os valores exatos.
            row = self.conn.execute("SELECT MIN(ts) FROM samples").fetchone()
            if row and row[0] is not None:
                first = (int(row[0]) // 60000) * 60000
                self.conn.execute("DELETE FROM agg_minute WHERE minute_ts >= ?", (first,))
                self.conn.execute("DELETE FROM meta WHERE key='agg_until'")
            log.info("Banco de dados atualizado para o esquema 3")

    # -------------------------------------------------------------- básico
    def close(self) -> None:
        with self._lock:
            try:
                self.conn.commit()
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self.conn.close()

    def commit(self) -> None:
        with self._lock:
            self.conn.commit()

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: Any) -> None:
        with self._lock:
            self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, str(value)))

    def size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                total += p.stat().st_size
        return total

    # ------------------------------------------------------------- sessões
    def start_session(self, ts: int, interval_ms: int, version: str, simulated: bool,
                      provider: str = "") -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO sessions(started_at, interval_ms, version, simulated, provider) VALUES (?,?,?,?,?)",
                (ts, interval_ms, version, int(simulated), provider or None))
            self.conn.commit()
            return int(cur.lastrowid)

    def set_session_provider(self, session_id: int, provider: str) -> None:
        with self._lock:
            self.conn.execute("UPDATE sessions SET provider=? WHERE id=?", (provider, session_id))

    def end_session(self, session_id: int, ts: int, reason: str) -> None:
        with self._lock:
            self.conn.execute("UPDATE sessions SET ended_at=?, end_reason=? WHERE id=?",
                              (ts, reason, session_id))
            self.conn.execute("UPDATE mode_periods SET ended_at=? WHERE session_id=? AND ended_at IS NULL",
                              (ts, session_id))
            self.conn.commit()

    def recover_previous(self) -> dict:
        """Fecha o que ficou aberto se o programa terminou de forma inesperada."""
        with self._lock:
            last_ts = self.last_sample_ts()
            info: dict[str, Any] = {"last_ts": last_ts, "crashed": False, "closed_events": 0,
                                    "last_session": None}
            row = self.conn.execute("SELECT * FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
            if row is None:
                return info
            info["last_session"] = dict(row)
            last_seen = last_ts if last_ts is not None else row["started_at"]
            if row["ended_at"] is None:
                info["crashed"] = True
                self.conn.execute("UPDATE sessions SET ended_at=?, end_reason=? WHERE id=?",
                                  (last_seen, "encerramento inesperado", row["id"]))
            self.conn.execute("UPDATE mode_periods SET ended_at=? WHERE ended_at IS NULL", (last_seen,))
            open_rows = self.conn.execute("SELECT * FROM events WHERE end_ts IS NULL").fetchall()
            for ev in open_rows:
                details = json.loads(ev["details"] or "{}")
                details["observacao"] = ("Encerrada sem confirmação de restabelecimento: o programa parou "
                                         "de medir. A duração vai até a última medição registrada.")
                self.conn.execute(
                    "UPDATE events SET end_ts=?, truncated=1, details=? WHERE id=?",
                    (max(last_seen, ev["start_ts"]), json.dumps(details, ensure_ascii=False), ev["id"]))
            info["closed_events"] = len(open_rows)
            self.conn.commit()
            return info

    # ------------------------------------------------------- modo conexão
    def open_mode_period(self, mode: str, ts: int, session_id: int, confirmed: bool) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO mode_periods(mode, started_at, confirmed, session_id) VALUES (?,?,?,?)",
                (mode, ts, int(confirmed), session_id))
            self.conn.commit()
            return int(cur.lastrowid)

    def close_mode_period(self, period_id: int, ts: int) -> None:
        with self._lock:
            self.conn.execute("UPDATE mode_periods SET ended_at=? WHERE id=?", (ts, period_id))
            self.conn.commit()

    def update_mode_period(self, period_id: int, mode: str, confirmed: bool) -> None:
        """Corrige o modo do período atual (usado quando o modo é informado logo no início)."""
        with self._lock:
            self.conn.execute("UPDATE mode_periods SET mode=?, confirmed=? WHERE id=?",
                              (mode, int(confirmed), period_id))
            self.conn.execute("UPDATE events SET mode=? WHERE mode_id=?", (mode, period_id))
            self.conn.commit()

    def get_mode_period(self, period_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM mode_periods WHERE id=?", (period_id,)).fetchone()
        return dict(row) if row else None

    # ----------------------------------------------------------- amostras
    def last_sample_ts(self) -> Optional[int]:
        row = self.conn.execute("SELECT MAX(ts) FROM samples").fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def insert_sample(self, ts: int, session_id: int, mode_id: Optional[int], status: int, conn: str,
                      lat: Optional[float], results: dict[str, ProbeResult], ext_lost: int,
                      ext_total: int, wifi: Optional[int]) -> int:
        if ts <= self._last_sample_ts:
            ts = self._last_sample_ts + 1
        self._last_sample_ts = ts
        vals: list[Any] = [ts, session_id, mode_id, status, CONN_CODE.get(conn, 0), lat]
        for col in TARGET_COLS:
            r = results.get(col)
            if r is None:
                vals += [None, None]
            else:
                vals += [r.rtt_ms, r.method]
        vals += [ext_lost, ext_total, wifi]
        with self._lock:
            self.conn.execute(
                "INSERT INTO samples(ts, session_id, mode_id, status, conn, lat, gw, gw_m, cf, cf_m, gg, gg_m,"
                " dish, dish_m, ext_lost, ext_total, wifi) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals)
        return ts

    @staticmethod
    def sample_to_dict(row: sqlite3.Row) -> dict:
        d = {"ts": row["ts"], "status": row["status"], "conn": CONN_NAME.get(row["conn"], ONLINE),
             "lat": row["lat"], "lost": row["ext_lost"], "total": row["ext_total"],
             "wifi": row["wifi"], "mode_id": row["mode_id"], "t": {}}
        for col in TARGET_COLS:
            d["t"][col] = {"rtt": row[col], "m": row[col + "_m"]}
        return d

    def samples_since(self, since_ts: int, limit: int = 5000) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM samples WHERE ts>=? ORDER BY ts LIMIT ?",
                                 (since_ts, limit)).fetchall()
        return [self.sample_to_dict(r) for r in rows]

    # ------------------------------------------------------------- eventos
    def insert_event(self, ev: Event, session_id: Optional[int]) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO events(type, start_ts, end_ts, precision_ms, session_id, mode, mode_id, wifi_signal,"
                " router_state, targets, lat_before, lat_after, diagnosis, diagnosis_text, details, truncated)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ev.type, ev.start_ts, ev.end_ts, ev.precision_ms, session_id, ev.mode, ev.mode_period_id,
                 ev.wifi_signal, ev.router_state, json.dumps(ev.targets, ensure_ascii=False), ev.lat_before,
                 ev.lat_after, ev.diagnosis, ev.diagnosis_text, json.dumps(ev.details, ensure_ascii=False),
                 int(ev.truncated)))
            ev.id = int(cur.lastrowid)
            return ev.id

    def update_event(self, ev: Event) -> None:
        if ev.id is None:
            return
        with self._lock:
            self.conn.execute(
                "UPDATE events SET end_ts=?, wifi_signal=?, router_state=?, targets=?, lat_before=?, lat_after=?,"
                " diagnosis=?, diagnosis_text=?, details=?, truncated=? WHERE id=?",
                (ev.end_ts, ev.wifi_signal, ev.router_state, json.dumps(ev.targets, ensure_ascii=False),
                 ev.lat_before, ev.lat_after, ev.diagnosis, ev.diagnosis_text,
                 json.dumps(ev.details, ensure_ascii=False), int(ev.truncated), ev.id))

    @staticmethod
    def event_to_dict(row: Any, now_ms: Optional[int] = None) -> dict:
        r = dict(row)
        end = r["end_ts"]
        is_open = end is None
        dur = None
        if end is not None:
            dur = max(0, end - r["start_ts"])
        elif now_ms is not None:
            dur = max(0, now_ms - r["start_ts"])
        return {
            "id": r["id"], "type": r["type"], "label": EVENT_LABEL.get(r["type"], r["type"]),
            "start_ts": r["start_ts"], "end_ts": end, "duration_ms": dur, "open": is_open,
            "precision_ms": r["precision_ms"], "mode": r["mode"],
            "mode_label": CONNECTION_MODES.get(r["mode"] or "", r["mode"] or ""),
            "wifi_signal": r["wifi_signal"], "router_state": r["router_state"],
            "targets": json.loads(r["targets"] or "{}"),
            "lat_before": r["lat_before"], "lat_after": r["lat_after"],
            "diagnosis": r["diagnosis"],
            "diagnosis_label": (json.loads(r["details"] or "{}").get("diagnostico_titulo")
                                or DIAGNOSIS_LABEL.get(r["diagnosis"] or "", "")),
            "diagnosis_text": r["diagnosis_text"], "details": json.loads(r["details"] or "{}"),
            "truncated": bool(r["truncated"]),
        }

    def list_events(self, types: Optional[Iterable[str]] = None, since: Optional[int] = None,
                    until: Optional[int] = None, limit: int = 100, now_ms: Optional[int] = None,
                    attach_dish: bool = True) -> list[dict]:
        sql = "SELECT * FROM events WHERE 1=1"
        args: list[Any] = []
        if types:
            types = list(types)
            sql += f" AND type IN ({','.join('?' * len(types))})"
            args += types
        if since is not None:
            sql += " AND (end_ts IS NULL OR end_ts >= ?)"
            args.append(since)
        if until is not None:
            sql += " AND start_ts <= ?"
            args.append(until)
        sql += " ORDER BY start_ts DESC, id DESC LIMIT ?"
        args.append(limit)
        evs = [self.event_to_dict(r, now_ms) for r in self.conn.execute(sql, args).fetchall()]
        if attach_dish:
            self.attach_dish(evs, now_ms)
        return evs

    # ------------------------------------------------- antena Starlink
    def insert_dish_outages(self, outages: list[dict], seen_at: int) -> list[dict]:
        """Grava as quedas informadas pela antena; devolve apenas as novas."""
        new = []
        with self._lock:
            for o in outages:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO dish_outages(start_ts, duration_ms, cause, did_switch, seen_at)"
                    " VALUES (?,?,?,?,?)",
                    (int(o["start_ts"]), int(o["duration_ms"]), str(o["cause"]), int(bool(o["did_switch"])),
                     seen_at))
                if cur.rowcount:
                    new.append(o)
        return new

    def insert_dish_stats(self, ts: int, w: dict) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO dish_stats(ts, samples, drop_s, full_drop_s, obstructed_s, full_obstructed_s,"
                " unscheduled_s, full_unscheduled_s, lat_mean_ms, power_w, down_bytes, up_bytes)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, w["samples"], w.get("drop_s"), w.get("full_drop_s"), w.get("obstructed_s"),
                 w.get("full_obstructed_s"), w.get("unscheduled_s"), w.get("full_unscheduled_s"),
                 w.get("lat_mean_ms"), w.get("power_w"), w.get("down_bytes"), w.get("up_bytes")))

    @staticmethod
    def dish_outage_to_dict(row: Any) -> dict:
        r = dict(row)
        info = cause_info(r["cause"], bool(r["did_switch"]))
        return {"start_ts": r["start_ts"], "end_ts": r["start_ts"] + r["duration_ms"],
                "duration_ms": r["duration_ms"], "did_switch": bool(r["did_switch"]), **info}

    def dish_outages_between(self, start: int, end: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM dish_outages WHERE start_ts < ? AND start_ts + duration_ms > ? ORDER BY start_ts",
            (end, start)).fetchall()
        return [self.dish_outage_to_dict(r) for r in rows]

    def has_dish_data(self) -> bool:
        return self.conn.execute("SELECT 1 FROM dish_outages LIMIT 1").fetchone() is not None or \
            self.conn.execute("SELECT 1 FROM dish_stats LIMIT 1").fetchone() is not None

    def attach_dish(self, evs: list[dict], now_ms: Optional[int] = None) -> list[dict]:
        """Acrescenta às quedas do monitor o motivo informado pela antena, quando houver."""
        targets = [e for e in evs if e.get("type") in FAILURE_TYPES]
        if not targets:
            return evs
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        lo = min(e["start_ts"] for e in targets) - DISH_MATCH_TOL_MS
        hi = max((e["end_ts"] if e["end_ts"] is not None else now) for e in targets) + DISH_MATCH_TOL_MS
        dish = self.dish_outages_between(lo, hi)
        if not dish:
            return evs
        for e in targets:
            s0 = e["start_ts"] - DISH_MATCH_TOL_MS
            e0 = (e["end_ts"] if e["end_ts"] is not None else now) + DISH_MATCH_TOL_MS
            hits = [d for d in dish if d["start_ts"] < e0 and d["end_ts"] > s0]
            if not hits:
                continue
            main = max(hits, key=lambda d: d["duration_ms"])
            e["dish"] = {**main, "count": len(hits),
                         "others": sorted({d["label"] for d in hits if d["label"] != main["label"]})}
        return evs

    def dish_cause_summary(self, start: int, end: int, monitor_events: Optional[list[dict]] = None) -> dict:
        """Quedas informadas pela antena no período, agrupadas por motivo."""
        outs = self.dish_outages_between(start, end)
        by: dict[str, dict] = {}
        for o in outs:
            b = by.setdefault(o["cause"], {"cause": o["cause"], "label": o["label"], "side": o["side"],
                                           "side_label": o["side_label"], "text": o["text"],
                                           "count": 0, "total_ms": 0, "max_ms": 0})
            b["count"] += 1
            b["total_ms"] += o["duration_ms"]
            b["max_ms"] = max(b["max_ms"], o["duration_ms"])
        causes = sorted(by.values(), key=lambda b: (-b["count"], -b["total_ms"]))
        sides: dict[str, int] = {}
        for o in outs:
            sides[o["side"]] = sides.get(o["side"], 0) + 1
        return {"count": len(outs), "total_ms": sum(o["duration_ms"] for o in outs), "causes": causes,
                "sides": sides, "outages": outs[-500:]}

    def dish_stats_summary(self, start: int, end: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT COUNT(*) n, SUM(samples) secs, SUM(drop_s) drop_s, SUM(full_drop_s) full_drop_s,"
            " SUM(obstructed_s) obstructed_s, SUM(full_obstructed_s) full_obstructed_s,"
            " SUM(unscheduled_s) unscheduled_s, SUM(full_unscheduled_s) full_unscheduled_s,"
            " SUM(lat_mean_ms*samples)/NULLIF(SUM(CASE WHEN lat_mean_ms IS NOT NULL THEN samples END),0) lat_mean_ms,"
            " AVG(power_w) power_w, SUM(down_bytes) down_bytes, SUM(up_bytes) up_bytes"
            " FROM dish_stats WHERE ts > ? AND ts - samples*1000 < ?", (start, end)).fetchone()
        if not row or not row["n"]:
            return None
        d = dict(row)
        secs = d["secs"] or 0
        d["drop_pct"] = (d["drop_s"] or 0) / secs * 100 if secs else None
        return d

    def last_event(self, etype: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM events WHERE type=? ORDER BY start_ts DESC LIMIT 1",
                                (etype,)).fetchone()
        return self.event_to_dict(row) if row else None

    # ---------------------------------------------------------- estatística
    def sample_stats(self, since: int, until: int) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*) n, AVG(lat) lat_avg, MIN(lat) lat_min, MAX(lat) lat_max,"
            " SUM(CASE WHEN conn<2 THEN ext_lost ELSE 0 END) lost,"
            " SUM(CASE WHEN conn<2 THEN ext_total ELSE 0 END) total"
            " FROM samples WHERE ts>=? AND ts<?", (since, until)).fetchone()
        return dict(row)

    def outage_time(self, etype: str, since: int, until: int, now_ms: int) -> tuple[int, int]:
        """(quantidade, milissegundos) de eventos do tipo dentro do intervalo, recortados nele."""
        rows = self.conn.execute(
            "SELECT start_ts, end_ts FROM events WHERE type=? AND start_ts < ? AND (end_ts IS NULL OR end_ts > ?)",
            (etype, until, since)).fetchall()
        count = 0
        total = 0
        for r in rows:
            s = max(r["start_ts"], since)
            e = min(r["end_ts"] if r["end_ts"] is not None else now_ms, until)
            if r["start_ts"] >= since:
                count += 1
            total += max(0, e - s)
        return count, total

    def monitored_ms(self, since: int, until: int) -> int:
        """Tempo efetivamente monitorado no intervalo: cada ciclo medido vale um
        intervalo da sessão em que foi medido. Períodos com o computador
        desligado, suspenso ou com o programa fechado não têm ciclos, então
        não entram na conta — nem como tempo online, nem como queda."""
        rows = self.conn.execute(
            "SELECT se.interval_ms iv, COUNT(*) n FROM samples sa JOIN sessions se ON se.id = sa.session_id"
            " WHERE sa.ts>=? AND sa.ts<? GROUP BY sa.session_id", (since, until)).fetchall()
        return int(sum(r["iv"] * r["n"] for r in rows))

    # ----------------------------------------------------------- manutenção
    def aggregate_minutes(self, until_ts: int) -> int:
        """Agrega os minutos completos ainda não agregados. Retorna minutos gravados."""
        last = self.get_meta("agg_until")
        start = int(last) if last else None
        if start is None:
            row = self.conn.execute("SELECT MIN(ts) FROM samples").fetchone()
            if not row or row[0] is None:
                return 0
            start = (int(row[0]) // 60000) * 60000
        # margem de 5 s: um ciclo iniciado no fim do minuto pode ainda estar sendo medido
        end = ((until_ts - 5000) // 60000) * 60000
        if end <= start:
            return 0
        with self._lock:
            cur = self.conn.execute(f"INSERT OR REPLACE INTO agg_minute({AGG_COLS}) {AGG_SELECT}", (start, end))
            self.set_meta("agg_until", end)
            self.conn.commit()
            return cur.rowcount if cur.rowcount is not None else 0

    def minute_rows(self, start: int, end: int) -> list[sqlite3.Row]:
        """Linhas por minuto no intervalo: minutos já agregados + os mais recentes,
        calculados na hora a partir dos dados brutos."""
        self.aggregate_minutes(int(time.time() * 1000))
        agg_until = int(self.get_meta("agg_until") or 0)
        rows = list(self.conn.execute(
            f"SELECT {AGG_COLS} FROM agg_minute WHERE minute_ts>=? AND minute_ts<? ORDER BY minute_ts",
            (start - start % 60000, min(end, agg_until) if agg_until else end)).fetchall())
        tail_start = max(start - start % 60000, agg_until)
        if tail_start < end:
            tail = self.conn.execute(
                f"SELECT * FROM ({AGG_SELECT}) ORDER BY 1", (tail_start, end)).fetchall()
            rows.extend(tail)
        return rows

    def raw_buckets(self, start: int, end: int, bucket_ms: int) -> list[sqlite3.Row]:
        """Agrupa dados brutos em intervalos menores que um minuto (períodos curtos)."""
        return self.conn.execute(
            "SELECT (ts/?)*? t, COUNT(*) n, AVG(lat) lat_avg, MIN(lat) lat_min, MAX(lat) lat_max,"
            " SUM(CASE WHEN conn<2 THEN ext_lost ELSE 0 END) lost_on,"
            " SUM(CASE WHEN conn<2 THEN ext_total ELSE 0 END) total_on,"
            " SUM(status=4) n_fail, SUM(conn=2) n_offline, MAX(status) worst"
            " FROM samples WHERE ts>=? AND ts<? GROUP BY ts/? ORDER BY 1",
            (bucket_ms, bucket_ms, start, end, bucket_ms)).fetchall()

    def samples_between(self, start: int, end: int, limit: int = 5000) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM samples WHERE ts>=? AND ts<? ORDER BY ts LIMIT ?",
                                 (start, end, limit)).fetchall()
        return [self.sample_to_dict(r) for r in rows]

    def mode_periods_between(self, start: int, end: int) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM mode_periods WHERE started_at < ? AND (ended_at IS NULL OR ended_at > ?)"
            " ORDER BY started_at", (end, start)).fetchall()]

    def sessions_between(self, start: int, end: int) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM sessions WHERE started_at < ? AND (ended_at IS NULL OR ended_at > ?)"
            " ORDER BY started_at", (end, start)).fetchall()]

    def first_sample_ts(self) -> Optional[int]:
        row = self.conn.execute("SELECT MIN(ts) FROM samples").fetchone()
        first = row[0] if row else None
        row = self.conn.execute("SELECT MIN(minute_ts) FROM agg_minute").fetchone()
        agg_first = row[0] if row else None
        vals = [v for v in (first, agg_first) if v is not None]
        return int(min(vals)) if vals else None

    def apply_retention(self, raw_before: int, agg_before: int, chunk: int = 20000) -> int:
        deleted = 0
        with self._lock:
            while True:
                cur = self.conn.execute(
                    "DELETE FROM samples WHERE ts IN (SELECT ts FROM samples WHERE ts < ? LIMIT ?)",
                    (raw_before, chunk))
                self.conn.commit()
                deleted += cur.rowcount
                if cur.rowcount < chunk:
                    break
            cur = self.conn.execute("DELETE FROM agg_minute WHERE minute_ts < ?", (agg_before,))
            deleted += cur.rowcount
            for table, col in (("dish_outages", "start_ts"), ("dish_stats", "ts")):
                deleted += self.conn.execute(f"DELETE FROM {table} WHERE {col} < ?", (agg_before,)).rowcount
            self.conn.commit()
            if deleted:
                self.conn.execute("PRAGMA incremental_vacuum(2000)")
                self.conn.commit()
        return deleted
