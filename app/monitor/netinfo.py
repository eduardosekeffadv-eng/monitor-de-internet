"""Gateway padrão e tipo da interface ativa (cabo / Wi-Fi), via iphlpapi.

GetBestRoute devolve a rota que o Windows usaria para chegar à internet;
o "próximo salto" dessa rota é o gateway padrão (o seu roteador). Com o
índice da interface, GetIfEntry2 informa se ela é Ethernet ou Wi-Fi.
São chamadas de microssegundos, sem processos externos.
"""

from __future__ import annotations

import ctypes
import socket
import struct
import sys
from ctypes import wintypes
from typing import Optional

from .types import NetInfo

_IFTYPE = {6: "cabo", 71: "wifi", 24: "loopback"}

if sys.platform == "win32":
    _iphlpapi = ctypes.WinDLL("iphlpapi.dll", use_last_error=True)

    class MIB_IPFORWARDROW(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in (
            "dwForwardDest", "dwForwardMask", "dwForwardPolicy", "dwForwardNextHop",
            "dwForwardIfIndex", "dwForwardType", "dwForwardProto", "dwForwardAge",
            "dwForwardNextHopAS", "dwForwardMetric1", "dwForwardMetric2",
            "dwForwardMetric3", "dwForwardMetric4", "dwForwardMetric5")]

    _GetBestRoute = _iphlpapi.GetBestRoute
    _GetBestRoute.restype = wintypes.DWORD
    _GetBestRoute.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(MIB_IPFORWARDROW)]

    try:
        _GetIfEntry2 = _iphlpapi.GetIfEntry2
        _GetIfEntry2.restype = wintypes.DWORD
        _GetIfEntry2.argtypes = [ctypes.c_void_p]
    except AttributeError:  # pragma: no cover
        _GetIfEntry2 = None

# Offsets em MIB_IF_ROW2 (estrutura estável desde o Windows Vista)
_OFF_INDEX = 8
_OFF_ALIAS = 28
_OFF_DESCRIPTION = 28 + 257 * 2
_OFF_TYPE = 1128
_ROW2_SIZE = 2048


def _ulong_to_ip(value: int) -> str:
    return socket.inet_ntoa(struct.pack("<I", value))


def _interface_info(if_index: int) -> tuple[str, str]:
    if sys.platform != "win32" or _GetIfEntry2 is None:
        return "desconhecido", ""
    buf = ctypes.create_string_buffer(_ROW2_SIZE)
    struct.pack_into("<I", buf, _OFF_INDEX, if_index)
    if _GetIfEntry2(buf) != 0:
        return "desconhecido", ""
    if_type = struct.unpack_from("<I", buf.raw, _OFF_TYPE)[0]
    alias = buf.raw[_OFF_ALIAS:_OFF_ALIAS + 514].decode("utf-16-le", "ignore").split("\x00", 1)[0]
    desc = buf.raw[_OFF_DESCRIPTION:_OFF_DESCRIPTION + 514].decode("utf-16-le", "ignore").split("\x00", 1)[0]
    name = alias or desc
    if desc and alias and desc != alias:
        name = f"{alias} ({desc})"
    return _IFTYPE.get(if_type, "outro"), name


class NetInfoReader:
    """Lê gateway/interface; guarda em cache os dados da interface."""

    def __init__(self, probe_dest: str = "8.8.8.8") -> None:
        self._dest = struct.unpack("<I", socket.inet_aton(probe_dest))[0]
        self._iface_cache: dict[int, tuple[str, str]] = {}

    def read(self) -> NetInfo:
        if sys.platform != "win32":
            return NetInfo()
        row = MIB_IPFORWARDROW()
        rc = _GetBestRoute(self._dest, 0, ctypes.byref(row))
        if rc != 0:
            # 1231 = ERROR_NETWORK_UNREACHABLE: sem rota padrão (cabo solto, Wi-Fi caiu)
            return NetInfo(gateway=None)
        gateway: Optional[str] = None
        if row.dwForwardNextHop:
            gateway = _ulong_to_ip(row.dwForwardNextHop)
        idx = int(row.dwForwardIfIndex)
        if idx not in self._iface_cache:
            self._iface_cache[idx] = _interface_info(idx)
        if_type, if_name = self._iface_cache[idx]
        return NetInfo(gateway=gateway, if_index=idx, if_type=if_type, if_name=if_name)

    def forget_interfaces(self) -> None:
        self._iface_cache.clear()
