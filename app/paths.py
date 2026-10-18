"""Caminhos do projeto. Tudo fica dentro da pasta do programa (portátil)."""

from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"
STATIC_DIR = WEB_DIR / "static"
DATA_DIR = Path(os.environ.get("MONITOR_INTERNET_DATA", ROOT_DIR / "data"))
LOG_DIR = DATA_DIR / "logs"
CONFIG_FILE = DATA_DIR / "config.json"


def database_file(simulation: bool = False) -> Path:
    # A simulação usa um banco separado para nunca misturar dados fictícios com reais.
    return DATA_DIR / ("simulacao.db" if simulation else "monitor.db")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
