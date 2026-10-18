"""Iniciar o monitoramento junto com o Windows, sem administrador.

Usa a chave do registro do próprio usuário
HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run, que o Windows executa
no login. O monitor inicia em segundo plano, sem abrir o Chrome.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .. import paths

log = logging.getLogger(__name__)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "MonitorDeInternet"


def supported() -> bool:
    return sys.platform == "win32"


def command() -> str:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else exe
    root = str(paths.ROOT_DIR)
    code = (f"import sys,os,runpy;os.chdir(r'{root}');sys.path.insert(0,r'{root}');"
            "runpy.run_module('app.main',run_name='__main__')")
    return f'"{runner}" -c "{code}"'


def is_enabled() -> bool:
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, VALUE_NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    """Liga/desliga. Retorna o estado final."""
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, command())
                log.info("Início automático com o Windows ativado")
            else:
                try:
                    winreg.DeleteValue(k, VALUE_NAME)
                except FileNotFoundError:
                    pass
                log.info("Início automático com o Windows desativado")
    except OSError as exc:
        log.error("Não foi possível alterar o início automático: %s", exc)
    return is_enabled()
