"""Sinal do Wi-Fi do computador, lido com `netsh wlan show interfaces`.

O netsh é executado no máximo a cada ~10 s (e imediatamente quando uma
falha começa), nunca a cada segundo. Funciona com o Windows em português
ou em inglês.

Limitação conhecida: a partir do Windows 11 24H2 o netsh pode exigir que
o serviço de Localização do Windows esteja ativado para mostrar dados do
Wi-Fi. Nesse caso o sinal é registrado como indisponível (nunca inventado)
e o painel explica como liberar.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unicodedata

from .types import WifiInfo

CREATE_NO_WINDOW = 0x08000000


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).strip().lower()


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        for enc in ("oem", "cp850", "cp1252"):
            try:
                return raw.decode(enc)
            except (LookupError, UnicodeDecodeError):
                continue
    return raw.decode("latin-1", "replace")


_KV = re.compile(r"^\s*([^:]+?)\s*:\s*(.*?)\s*$")


def parse_netsh(output: str) -> WifiInfo:
    low = _norm(output)
    if "localiza" in low or "location" in low:
        if "signal" not in low and "sinal" not in low:
            return WifiInfo(available=False, error="localizacao")
    if ("no wireless interface" in low or "nao ha interface sem fio" in low
            or "servico de configuracao automatica de wlan" in low or "wlan autoconfig" in low):
        return WifiInfo(available=False, error="sem_interface")

    info = WifiInfo(available=False)
    for line in output.splitlines():
        m = _KV.match(line)
        if not m:
            continue
        key, value = _norm(m.group(1)), m.group(2)
        if key in ("name", "nome") and not info.interface:
            info.interface = value
            info.available = True
        elif key in ("state", "estado"):
            info.connected = _norm(value) in ("connected", "conectado")
        elif key == "ssid":
            info.ssid = value
        elif key in ("signal", "sinal"):
            pm = re.search(r"(\d+)\s*%", value)
            if pm:
                info.signal_pct = int(pm.group(1))
        elif key == "rssi":
            rm = re.search(r"-?\d+", value)
            if rm:
                info.rssi_dbm = int(rm.group(0))
    if not info.connected:
        info.signal_pct = None
        info.rssi_dbm = None
    return info


def read_wifi(timeout_s: float = 4.0) -> WifiInfo:
    """Executa o netsh (bloqueante; o motor chama em uma thread)."""
    if sys.platform != "win32":
        return WifiInfo(available=False, error="nao_windows")
    try:
        proc = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, timeout=timeout_s, creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return WifiInfo(available=False, error=f"netsh: {exc}")
    text = _decode(proc.stdout or b"") + "\n" + _decode(proc.stderr or b"")
    return parse_netsh(text)
