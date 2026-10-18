"""Formatação em português brasileiro (usada nos textos gerados pelo servidor)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional


def local(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000).astimezone()


def fmt_date(ms: int) -> str:
    return local(ms).strftime("%d/%m/%Y")


def fmt_datetime(ms: int, seconds: bool = False) -> str:
    return local(ms).strftime("%d/%m/%Y %H:%M:%S" if seconds else "%d/%m/%Y %H:%M")


def fmt_time(ms: int) -> str:
    return local(ms).strftime("%H:%M:%S")


def fmt_duration(ms: Optional[float]) -> str:
    if ms is None:
        return "—"
    s = max(0, int(round(ms / 1000)))
    if s < 60:
        return f"{s} s"
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d} d {h} h"
    if h:
        return f"{h} h {m:02d} min"
    return f"{m} min {s:02d} s"


def fmt_num(v: Optional[float], decimals: int = 0) -> str:
    if v is None:
        return "—"
    txt = f"{v:,.{decimals}f}"
    return txt.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_pct(v: Optional[float], decimals: int = 2) -> str:
    return "—" if v is None else f"{fmt_num(v, decimals)}%"


def plural(n: int, singular: str, plural_form: str) -> str:
    return f"{fmt_num(n)} {singular if n == 1 else plural_form}"


WEEKDAYS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MONTHS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
          "outubro", "novembro", "dezembro"]
