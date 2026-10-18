"""Tipos de conexão de internet e o equipamento do provedor de cada um.

O "equipamento do provedor" é o aparelho entre o seu roteador e a rede da
operadora (antena Starlink, ONT de fibra, modem a cabo...). Quando ele é
alcançável pela rede, o diagnóstico consegue separar "falha entre o
roteador e o equipamento" de "equipamento respondendo, mas sem internet".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    label: str            # nome do tipo de conexão
    hint: str             # explicação curta no seletor
    equip_name: str       # nome do equipamento do provedor
    equip_short: str      # nome curto (gráfico/chips)
    equip_ref: str        # com artigo, para frases ("a antena Starlink")
    default_host: str     # "" = sem endereço padrão (fica desativado até ser informado)
    tcp_port: int
    upstream_text: str    # o que fica além do equipamento, para o diagnóstico
    lat_normal_ms: float  # limites recomendados para o tipo
    lat_elevated_ms: float


PROVIDERS: dict[str, ProviderProfile] = {p.id: p for p in (
    ProviderProfile("starlink", "Starlink", "Internet via satélite de órbita baixa",
                    "Antena Starlink", "Antena", "a antena Starlink", "192.168.100.1", 9200,
                    "enlace via satélite ou rede da Starlink", 100, 250),
    ProviderProfile("fibra", "Fibra óptica", "ONT/modem de fibra da operadora",
                    "Modem/ONT de fibra", "ONT", "o modem/ONT de fibra", "192.168.100.1", 80,
                    "rede da operadora de fibra", 60, 150),
    ProviderProfile("cabo", "Cabo coaxial", "Modem a cabo (TV a cabo/DOCSIS)",
                    "Modem a cabo", "Modem", "o modem a cabo", "192.168.100.1", 80,
                    "rede da operadora a cabo", 60, 150),
    ProviderProfile("radio", "Via rádio", "Antena/rádio do provedor local",
                    "Rádio do provedor", "Rádio", "o rádio do provedor", "", 80,
                    "enlace de rádio ou rede do provedor", 80, 200),
    ProviderProfile("movel", "4G/5G", "Internet móvel (modem ou roteador com chip)",
                    "Modem 4G/5G", "Modem", "o modem 4G/5G", "", 80,
                    "rede móvel da operadora", 120, 300),
    ProviderProfile("satelite_geo", "Outro satélite", "Satélite geoestacionário (HughesNet, Viasat…)",
                    "Modem de satélite", "Modem", "o modem de satélite", "", 80,
                    "enlace via satélite ou rede da operadora", 800, 1200),
    ProviderProfile("outro", "Outro", "DSL, conexão corporativa ou outro tipo",
                    "Equipamento do provedor", "Provedor", "o equipamento do provedor", "", 80,
                    "rede da operadora", 100, 250),
)}

DEFAULT_PROVIDER = "starlink"


def profile(provider_type: str) -> ProviderProfile:
    """Perfil do tipo de conexão; "" (ainda não escolhido) usa textos genéricos."""
    return PROVIDERS.get(provider_type, PROVIDERS["outro"])


def public_profiles() -> list[dict]:
    return [asdict(p) for p in PROVIDERS.values()]
