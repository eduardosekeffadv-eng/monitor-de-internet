"""Logs de execução em data/logs/monitor.log (rotativo, até ~25 MB)."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import paths


def setup_logging(level: int = logging.INFO, console: bool = False) -> None:
    paths.ensure_dirs()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    fh = RotatingFileHandler(paths.LOG_DIR / "monitor.log", maxBytes=5 * 1024 * 1024,
                             backupCount=4, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    if console and sys.stderr is not None:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(fmt)
        root.addHandler(ch)

    # Bibliotecas barulhentas
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


def install_excepthook() -> None:
    def hook(exc_type, exc, tb):
        logging.getLogger("app").critical("Erro não tratado", exc_info=(exc_type, exc, tb))
    sys.excepthook = hook
