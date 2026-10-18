"""Ponto de entrada do processo de monitoramento.

Normalmente é iniciado pelo iniciar_monitoramento.bat (via app.launcher),
em segundo plano e sem janela. Também pode ser executado diretamente:

    .venv\\Scripts\\python -m app.main --console      (mostra o log na tela)
    .venv\\Scripts\\python -m app.main --simulacao    (dados fictícios, para demonstração)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import urllib.request

from . import APP_NAME, __version__, paths
from .logging_setup import install_excepthook, setup_logging

log = logging.getLogger("app")


def show_error(message: str) -> None:
    log.error(message)
    if sys.stderr is not None:
        try:
            print(f"\n[ERRO] {message}\n", file=sys.stderr)
        except Exception:
            pass
    if sys.platform == "win32" and (sys.stdout is None or os.environ.get("MONITOR_INTERNET_GUI_ERRORS")):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
        except Exception:
            pass


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if sys.platform != "win32":
            # ignora conexões em TIME_WAIT (no Windows esta opção teria outro efeito)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def running_instance(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
            # "starlink-monitor" = versões 1.x, antes da mudança de nome
            if data.get("app") in ("monitor-de-internet", "starlink-monitor"):
                return data
    except Exception:
        return None
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--console", action="store_true", help="mostra o log no terminal")
    parser.add_argument("--simulacao", action="store_true",
                        help="usa dados FICTÍCIOS (demonstração/testes), em banco separado")
    args = parser.parse_args(argv)

    # pythonw não tem console: evita falhas de bibliotecas que escrevem na tela
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
        os.environ.setdefault("MONITOR_INTERNET_GUI_ERRORS", "1")

    paths.ensure_dirs()
    setup_logging(console=args.console)
    install_excepthook()
    simulated = args.simulacao or os.environ.get("MONITOR_INTERNET_SIMULACAO") == "1"
    log.info("%s %s iniciando (Python %s, simulação=%s)", APP_NAME, __version__,
             sys.version.split()[0], simulated)

    if sys.platform != "win32" and not simulated:
        show_error("O monitoramento real usa APIs do Windows. Neste sistema só o modo --simulacao está disponível.")
        return 2

    from .config import SettingsStore
    store = SettingsStore()
    port = store.settings.port

    if not port_is_free(port):
        if running_instance(port):
            log.info("Já existe uma instância em execução na porta %d; nada a fazer.", port)
            return 0
        show_error(f"A porta {port} já está sendo usada por outro programa.\n\n"
                   f"Feche o programa que usa a porta {port} ou altere \"port\" em data\\config.json.")
        return 3

    try:
        import uvicorn

        from .api.server import create_app
        from .monitor.probers import RealProber, SimulatedProber
        from .storage.db import Database
    except ImportError as exc:
        show_error(f"Dependência ausente ({exc}). Execute o instalar.bat novamente.")
        return 4

    try:
        db = Database(paths.database_file(simulated))
    except Exception as exc:
        show_error(f"Não foi possível abrir o banco de dados: {exc}")
        return 5

    try:
        prober = SimulatedProber() if simulated else RealProber()
    except Exception as exc:
        show_error(f"Não foi possível inicializar as verificações de rede: {exc}")
        return 6

    holder: dict = {}

    def request_shutdown() -> None:
        srv = holder.get("server")
        if srv is not None:
            srv.should_exit = True

    app = create_app(store, db, prober, request_shutdown=request_shutdown)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False,
                            ws="auto", lifespan="on", timeout_graceful_shutdown=5)
    server = uvicorn.Server(config)
    holder["server"] = server
    try:
        server.run()
    except Exception as exc:
        show_error(f"O servidor parou por um erro: {exc}")
        return 7
    log.info("Processo encerrado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
