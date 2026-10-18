"""Relatórios em HTML.

O relatório é um único arquivo HTML que traz embutidos os dados, o estilo,
as fontes e a biblioteca de gráficos (Chart.js). Assim, o mesmo arquivo
serve para visualizar no navegador, salvar e abrir depois sem o programa,
e imprimir (Imprimir → Salvar como PDF no Chrome).
"""

from __future__ import annotations

import base64
import html
import json
import re
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Optional

from .. import APP_NAME, __version__, paths
from ..config import CONNECTION_MODES, Settings
from ..events.detector import (E_INACTIVE, E_LATENCY, E_LOSS, E_MICRO, E_MODE, E_OUTAGE, E_PROVIDER,
                               E_START, E_STOP)
from ..services import analytics
from ..services.formatting import MONTHS, WEEKDAYS, fmt_date, fmt_datetime
from ..storage.db import Database

KINDS = {
    "diario": "Relatório diário",
    "semanal": "Relatório semanal",
    "mensal": "Relatório mensal",
    "personalizado": "Relatório de período personalizado",
}
MAX_SPAN_DAYS = 400


class PeriodError(ValueError):
    pass


def _ms(d: datetime) -> int:
    return int(d.timestamp() * 1000)


def resolve_period(kind: str, date: Optional[str] = None, start: Optional[int] = None,
                   end: Optional[int] = None) -> tuple[int, int, str]:
    """Retorna (início, fim, descrição do período) no horário local."""
    if kind not in KINDS:
        raise PeriodError("tipo de relatório inválido")
    if kind == "personalizado":
        if start is None or end is None:
            raise PeriodError("informe o início e o fim do período")
        if end <= start:
            raise PeriodError("o fim do período precisa ser depois do início")
        if end - start > MAX_SPAN_DAYS * analytics.DAY:
            raise PeriodError(f"o período máximo é de {MAX_SPAN_DAYS} dias")
        label = f"{fmt_datetime(start)} a {fmt_datetime(end - 1)}"
        return start, end, label
    try:
        base = datetime.strptime(date, "%Y-%m-%d") if date else datetime.now()
    except ValueError:
        raise PeriodError("data inválida (use AAAA-MM-DD)")
    day = datetime(base.year, base.month, base.day)
    if kind == "diario":
        s, e = day, day + timedelta(days=1)
        label = f"{WEEKDAYS[s.weekday()].capitalize()}, {s.day} de {MONTHS[s.month - 1]} de {s.year}"
    elif kind == "semanal":
        s = day - timedelta(days=day.weekday())
        e = s + timedelta(days=7)
        last = e - timedelta(days=1)
        label = f"Semana de {s.strftime('%d/%m/%Y')} (segunda) a {last.strftime('%d/%m/%Y')} (domingo)"
    else:
        s = datetime(day.year, day.month, 1)
        e = datetime(day.year + (day.month == 12), day.month % 12 + 1, 1)
        label = f"{MONTHS[s.month - 1].capitalize()} de {s.year}"
    return _ms(s), _ms(e), label


def report_data(db: Database, s: Settings, provider: dict, kind: str, start: int, end: int,
                period_label: str, simulated: bool = False) -> dict:
    a = analytics.analysis(db, s, start, end)
    now = a["now"]
    end_eff = min(end, now)
    outages = db.list_events(types=[E_OUTAGE], since=start, until=end_eff, limit=100000, now_ms=now)
    outages = sorted((e for e in outages if (e["end_ts"] or now) > start), key=lambda e: e["start_ts"])
    micros = sorted((e for e in db.list_events(types=[E_MICRO], since=start, until=end_eff, limit=100000,
                                                 now_ms=now) if e["start_ts"] >= start),
                    key=lambda e: e["start_ts"])
    timeline = sorted(db.list_events(
        types=[E_OUTAGE, E_MICRO, E_INACTIVE, E_LOSS, E_LATENCY, E_MODE, E_PROVIDER, E_START, E_STOP],
        since=start, until=end_eff, limit=5000, now_ms=now), key=lambda e: e["start_ts"])
    sessions = db.sessions_between(start, end_eff)
    providers_used = sorted({x["provider"] for x in sessions if x.get("provider")})
    st = a["stats"]
    return {
        "meta": {
            "app": APP_NAME, "version": __version__,
            "kind": kind, "title": KINDS[kind], "period_label": period_label,
            "start": start, "end": end, "end_effective": end_eff, "in_progress": end > now,
            "generated_at": now, "generated_label": fmt_datetime(now, True),
            "provider": provider, "providers_used": providers_used,
            "modes": CONNECTION_MODES, "simulated": simulated,
            "criteria": {
                "interval_ms": s.interval_ms, "timeout_ms": s.timeout_ms,
                "lat_normal_max_ms": s.lat_normal_max_ms, "lat_elevated_max_ms": s.lat_elevated_max_ms,
                "loss_threshold_pct": s.loss_threshold_pct, "loss_window_s": s.loss_window_s,
                "outage_confirm_cycles": s.outage_confirm_cycles,
                "targets": [{"name": t.name, "host": t.host, "enabled": t.enabled} for t in s.targets],
            },
        },
        "summary": analytics.summary_text(st, outages, a["dish"]),
        "stats": st,
        "series": a["series"],
        "bands": a["bands"],
        "by_mode": a["by_mode"],
        "by_day": a["by_day"],
        "heatmap": a["heatmap"],
        "outages": outages,
        "micros": micros,
        "timeline": timeline,
        "thresholds": a["thresholds"],
        "dish": a["dish"],
    }


def filename(kind: str, start: int, end: int) -> str:
    s = datetime.fromtimestamp(start / 1000)
    if kind == "diario":
        tag = s.strftime("%Y-%m-%d")
    elif kind == "semanal":
        tag = "semana-" + s.strftime("%Y-%m-%d")
    elif kind == "mensal":
        tag = s.strftime("%Y-%m")
    else:
        tag = s.strftime("%Y-%m-%d_%Hh%M") + "_a_" + datetime.fromtimestamp((end - 1) / 1000).strftime(
            "%Y-%m-%d_%Hh%M")
    return f"relatorio-internet-{kind}-{tag}.html"


# ----------------------------------------------------------- montagem
@lru_cache(maxsize=1)
def _fonts_css() -> str:
    faces = [("Inter", 400, "inter-latin-400-normal.woff2"), ("Inter", 500, "inter-latin-500-normal.woff2"),
             ("Inter", 600, "inter-latin-600-normal.woff2"), ("Inter", 700, "inter-latin-700-normal.woff2"),
             ("JetBrains Mono", 400, "jetbrains-mono-latin-400-normal.woff2"),
             ("JetBrains Mono", 600, "jetbrains-mono-latin-600-normal.woff2")]
    out = []
    for fam, w, f in faces:
        data = base64.b64encode((paths.STATIC_DIR / "fonts" / f).read_bytes()).decode("ascii")
        out.append(f'@font-face{{font-family:"{fam}";font-weight:{w};font-display:swap;'
                   f'src:url(data:font/woff2;base64,{data}) format("woff2")}}')
    return "\n".join(out)


def _read(rel: str) -> str:
    return (paths.WEB_DIR / rel).read_text(encoding="utf-8")


def _script_safe(text: str) -> str:
    # impede que o conteúdo feche a tag <script> antes da hora
    return text.replace("</script", "<\\/script").replace("<!--", "<\\!--")


def build_html(data: dict, mode: str = "view") -> str:
    """mode: "view" (aberto pelo sistema, com botões) ou "file" (arquivo salvo)."""
    template = _read("report/report.html")
    data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    title = f"{data['meta']['title']} — {data['meta']['period_label']}"
    replacements = {
        "TITLE": html.escape(title),
        "FONTS_CSS": _fonts_css(),
        "REPORT_CSS": (_read("static/css/period.css") + "\n" + _read("static/css/dish.css") + "\n"
                       + _read("report/report.css")),
        "PERIOD_JS": _script_safe(_read("static/js/period-charts.js") + "\n" + _read("static/js/dish-view.js")),
        "CHARTJS": _script_safe(_read("static/vendor/chart.umd.min.js")),
        "REPORT_JS": _script_safe(_read("report/report.js")),
        "DATA_JSON": _script_safe(data_json),
        "MODE": mode,
    }
    # substituição em uma única passada (o conteúdo inserido nunca é reprocessado)
    return re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: replacements.get(m.group(1), m.group(0)), template)
