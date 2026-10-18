"""ICMP (ping) pela API nativa do Windows, sem abrir ping.exe e sem administrador.

Usa IcmpCreateFile / IcmpSendEcho da iphlpapi.dll — a mesma API que o
próprio ping.exe usa internamente. A chamada é bloqueante, por isso o
motor a executa em threads (uma por destino), em paralelo.

Limitação conhecida: o Windows informa o tempo de ida e volta com
resolução de 1 ms. Respostas mais rápidas que isso (comum no roteador
local) vêm como 0 ms e são exibidas como "<1 ms" — nunca confundidas com
falha, que é registrada separadamente.
"""

from __future__ import annotations

import ctypes
import socket
import struct
import sys
import threading
from ctypes import wintypes

from .types import M_FAIL, M_ICMP, ProbeResult

if sys.platform != "win32":  # pragma: no cover - só existe no Windows
    raise ImportError("icmp_windows só funciona no Windows")

_iphlpapi = ctypes.WinDLL("iphlpapi.dll", use_last_error=True)

_IcmpCreateFile = _iphlpapi.IcmpCreateFile
_IcmpCreateFile.restype = wintypes.HANDLE
_IcmpCreateFile.argtypes = []

_IcmpCloseHandle = _iphlpapi.IcmpCloseHandle
_IcmpCloseHandle.restype = wintypes.BOOL
_IcmpCloseHandle.argtypes = [wintypes.HANDLE]

_IcmpSendEcho = _iphlpapi.IcmpSendEcho
_IcmpSendEcho.restype = wintypes.DWORD
_IcmpSendEcho.argtypes = [
    wintypes.HANDLE,   # IcmpHandle
    wintypes.ULONG,    # DestinationAddress (IPAddr, ordem de rede)
    wintypes.LPVOID,   # RequestData
    wintypes.WORD,     # RequestSize
    wintypes.LPVOID,   # RequestOptions (NULL)
    wintypes.LPVOID,   # ReplyBuffer
    wintypes.DWORD,    # ReplySize
    wintypes.DWORD,    # Timeout (ms)
]

INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

# Códigos de status IP_* mais comuns
_STATUS_TEXT = {
    11002: "rede de destino inalcançável",
    11003: "host de destino inalcançável",
    11010: "tempo esgotado",
    11013: "TTL expirado",
    11050: "falha geral",
}
IP_SUCCESS = 0
IP_REQ_TIMED_OUT = 11010

_PAYLOAD = b"monitor-internet"           # 16 bytes: tráfego mínimo
_REPLY_SIZE = 64 + len(_PAYLOAD) + 8 + 64  # folga para mensagens de erro ICMP
_local = threading.local()


def _handle() -> int:
    h = getattr(_local, "handle", None)
    if h is None:
        h = _IcmpCreateFile()
        if not h or h == INVALID_HANDLE_VALUE:
            raise OSError(ctypes.get_last_error(), "IcmpCreateFile falhou")
        _local.handle = h
    return h


def _ip_to_ulong(ip: str) -> int:
    # IPAddr é o endereço em ordem de rede lido como inteiro little-endian
    return struct.unpack("<I", socket.inet_aton(ip))[0]


def ping(ip: str, timeout_ms: int) -> ProbeResult:
    """Envia um único echo ICMP e devolve o resultado (bloqueante)."""
    try:
        addr = _ip_to_ulong(ip)
    except OSError:
        return ProbeResult(None, M_FAIL, "endereço inválido")
    try:
        handle = _handle()
    except OSError as exc:
        return ProbeResult(None, M_FAIL, f"ICMP indisponível: {exc}")

    request = ctypes.create_string_buffer(_PAYLOAD, len(_PAYLOAD))
    reply = ctypes.create_string_buffer(_REPLY_SIZE)
    n = _IcmpSendEcho(handle, addr, request, len(_PAYLOAD), None, reply, _REPLY_SIZE, int(timeout_ms))
    if n == 0:
        err = ctypes.get_last_error()
        return ProbeResult(None, M_FAIL, _STATUS_TEXT.get(err, f"erro {err}"))

    # Os três primeiros campos de ICMP_ECHO_REPLY (Address, Status, RoundTripTime)
    # têm o mesmo layout em 32 e 64 bits.
    _address, status, rtt = struct.unpack_from("<III", reply.raw, 0)
    if status != IP_SUCCESS:
        return ProbeResult(None, M_FAIL, _STATUS_TEXT.get(status, f"status {status}"))
    return ProbeResult(float(rtt), M_ICMP)


def self_test() -> tuple[bool, str]:
    """Verifica se a API ICMP funciona (usada pelo instalador)."""
    r = ping("127.0.0.1", 1000)
    if r.ok:
        return True, "ICMP nativo funcionando"
    return False, r.error or "sem resposta do 127.0.0.1"
