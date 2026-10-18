"""Inicializador usado pelos arquivos .bat.

    python -m app.launcher iniciar     inicia em segundo plano e abre o Chrome
    python -m app.launcher parar       encerra o monitoramento com segurança
    python -m app.launcher status      mostra se está em execução
    python -m app.launcher verificar   testa o ICMP nativo (usado pelo instalador)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import paths
from .main import port_is_free, running_instance

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def _port() -> int:
    try:
        data = json.loads(paths.CONFIG_FILE.read_text(encoding="utf-8"))
        return int(data.get("port", 8000))
    except Exception:
        return 8000


def _find_chrome() -> str | None:
    candidates = []
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def open_dashboard(port: int) -> None:
    url = f"http://localhost:{port}"
    chrome = _find_chrome()
    if chrome:
        subprocess.Popen([chrome, url], close_fds=True)
        print(f"  Painel aberto no Google Chrome: {url}")
    else:
        webbrowser.open(url)
        print(f"  Google Chrome não encontrado; painel aberto no navegador padrão: {url}")


def _tail_log(lines: int = 15) -> str:
    log_file = paths.LOG_DIR / "monitor.log"
    if not log_file.exists():
        return ""
    try:
        return "\n".join(log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def start(open_browser: bool = True) -> int:
    port = _port()
    if running_instance(port):
        print("  O monitoramento já está em execução.")
        if open_browser:
            open_dashboard(port)
        return 0
    if not port_is_free(port):
        print(f"\n  [ERRO] A porta {port} está ocupada por outro programa.")
        print(f"  Feche esse programa ou altere \"port\" em data\\config.json e tente de novo.")
        return 3

    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = str(pythonw if pythonw.exists() else exe)
    flags = 0
    if sys.platform == "win32":
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    proc = subprocess.Popen([runner, "-m", "app.main"], cwd=str(paths.ROOT_DIR), creationflags=flags,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            close_fds=True)
    print("  Iniciando o monitoramento em segundo plano", end="", flush=True)
    for _ in range(60):
        time.sleep(0.5)
        print(".", end="", flush=True)
        if running_instance(port):
            print(" pronto!")
            if open_browser:
                open_dashboard(port)
            print("\n  O monitoramento continua mesmo se você fechar o Chrome ou esta janela.")
            print("  Para encerrar, use o parar_monitoramento.bat.")
            return 0
        if proc.poll() is not None:
            break
    print("\n\n  [ERRO] O monitoramento não iniciou. Últimas linhas do log (data\\logs\\monitor.log):\n")
    print(_tail_log() or "  (log vazio)")
    return 1


def stop() -> int:
    port = _port()
    if not running_instance(port):
        print("  O monitoramento não está em execução.")
        return 0
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", method="POST", data=b"")
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as exc:
        print(f"  [ERRO] Não foi possível pedir o encerramento: {exc}")
        return 1
    print("  Encerrando", end="", flush=True)
    for _ in range(40):
        time.sleep(0.5)
        print(".", end="", flush=True)
        if running_instance(port) is None:
            print(" monitoramento encerrado com segurança.")
            return 0
    print("\n  [AVISO] O processo ainda não terminou; aguarde alguns segundos.")
    return 1


def status() -> int:
    info = running_instance(_port())
    if info:
        print(f"  Em execução (versão {info.get('version')}, {info.get('cycles')} ciclos nesta sessão).")
        return 0
    print("  Não está em execução.")
    return 1


def verify() -> int:
    if sys.platform != "win32":
        print("  Verificação do ICMP disponível apenas no Windows.")
        return 0
    from .monitor.icmp_windows import self_test
    ok, msg = self_test()
    print(f"  {'OK' if ok else 'FALHA'}: {msg}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    cmd = args[0] if args else "iniciar"
    if cmd == "iniciar":
        return start()
    if cmd == "parar":
        return stop()
    if cmd == "status":
        return status()
    if cmd == "verificar":
        return verify()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
