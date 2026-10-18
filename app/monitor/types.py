"""Estruturas de dados compartilhadas pelo motor de medição."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Códigos de método gravados no banco
M_NOT_PROBED = None   # destino não testado neste ciclo (desativado / indisponível)
M_FAIL = 0            # testado e sem resposta
M_ICMP = 1            # respondeu via ICMP (ping)
M_TCP = 2             # respondeu via conexão TCP

METHOD_LABEL = {None: "não testado", 0: "sem resposta", 1: "ICMP", 2: "TCP"}

# Status de cada amostra (ciclo)
S_OK = 0          # latência normal
S_ELEVATED = 1    # latência elevada
S_CRITICAL = 2    # latência acima do limite elevado
S_PARTIAL = 3     # parte dos destinos externos sem resposta (perda de pacotes)
S_FAIL = 4        # nenhum destino externo respondeu

# Status geral da conexão
ONLINE = "online"
INSTAVEL = "instavel"
OFFLINE = "offline"


@dataclass
class ProbeResult:
    rtt_ms: Optional[float]           # None quando não houve resposta
    method: Optional[int]             # M_* acima
    error: str = ""

    @property
    def probed(self) -> bool:
        return self.method is not None

    @property
    def ok(self) -> bool:
        return self.method in (M_ICMP, M_TCP)

    @staticmethod
    def not_probed() -> "ProbeResult":
        return ProbeResult(None, M_NOT_PROBED)


@dataclass
class WifiInfo:
    available: bool                   # existe interface Wi-Fi e foi possível ler
    connected: bool = False
    signal_pct: Optional[int] = None
    rssi_dbm: Optional[int] = None
    ssid: str = ""
    interface: str = ""
    error: str = ""                   # ex.: "localizacao" quando o Windows bloqueia


@dataclass
class NetInfo:
    gateway: Optional[str] = None     # IPv4 do gateway padrão, None se não houver rota
    if_index: Optional[int] = None
    if_type: str = "desconhecido"     # "cabo", "wifi", "outro", "desconhecido"
    if_name: str = ""


@dataclass
class Cycle:
    ts: int                                       # epoch em milissegundos (início do ciclo)
    results: dict[str, ProbeResult]
    net: NetInfo = field(default_factory=NetInfo)
    wifi: Optional[WifiInfo] = None
    mode: str = "cabo"
    mode_period_id: Optional[int] = None
