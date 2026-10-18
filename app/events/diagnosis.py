"""Diagnóstico provável de uma falha.

As regras não presumem que toda falha é do provedor: primeiro descartam o
Wi-Fi do computador e a rede local; só então apontam o acesso à internet.
Todos os textos dizem "provável"/"possível", porque o diagnóstico é uma
inferência a partir das respostas, não uma certeza.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..config import WIFI_MODES
from ..providers import ProviderProfile, profile

D_WIFI = "wifi"
D_LOCAL = "rede_local"
D_NO_ROUTE = "sem_rota"
D_INTERNET = "internet"
D_UPSTREAM = "internet_antena_ok"     # equipamento do provedor responde, internet não (código mantido da v1)
D_DISH_LINK = "enlace_antena"         # roteador ok, equipamento do provedor e internet não
D_UNKNOWN = "indeterminado"

DIAGNOSIS_LABEL = {
    D_WIFI: "Possível problema de Wi-Fi",
    D_LOCAL: "Possível problema na rede local",
    D_NO_ROUTE: "Computador sem conexão com a rede",
    D_INTERNET: "Provável falha de acesso à internet",
    D_UPSTREAM: "Provável falha na rede do provedor",
    D_DISH_LINK: "Provável falha entre o roteador e o equipamento do provedor",
    D_UNKNOWN: "Causa indeterminada",
}


def diagnosis_title(code: str, prof: ProviderProfile | None = None) -> str:
    """Título do diagnóstico ajustado ao tipo de conexão."""
    if prof is not None:
        if code == D_UPSTREAM:
            return ("Provável falha no acesso à internet via satélite" if prof.id in ("starlink", "satelite_geo")
                    else "Provável falha na rede do provedor")
        if code == D_DISH_LINK:
            return f"Provável falha entre o roteador e {prof.equip_ref}"
    return DIAGNOSIS_LABEL.get(code, "")


@dataclass
class FailureStats:
    """Contadores acumulados durante uma falha (microqueda ou interrupção)."""

    cycles: int = 0
    gw_probed: int = 0
    gw_fail: int = 0
    no_route: int = 0
    dish_probed: int = 0
    dish_ok: int = 0
    wifi_min_signal: Optional[int] = None
    wifi_disconnected: int = 0
    wifi_samples: int = 0
    target_fail: dict[str, int] = field(default_factory=dict)
    target_probed: dict[str, int] = field(default_factory=dict)

    def router_state(self) -> str:
        if self.no_route and self.no_route >= self.cycles / 2:
            return "sem rota de rede (computador desconectado)"
        if self.gw_probed == 0:
            return "não identificado"
        if self.gw_fail == 0:
            return "respondendo normalmente"
        if self.gw_fail >= self.gw_probed:
            return "sem resposta"
        return f"intermitente ({self.gw_fail} de {self.gw_probed} verificações sem resposta)"

    def target_states(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for tid, probed in self.target_probed.items():
            fails = self.target_fail.get(tid, 0)
            if probed == 0:
                continue
            if fails == 0:
                out[tid] = "respondendo"
            elif fails >= probed:
                out[tid] = "sem resposta"
            else:
                out[tid] = f"intermitente ({fails}/{probed})"
        return out


def diagnose(stats: FailureStats, mode: str, weak_signal_pct: int,
             prof: ProviderProfile | None = None) -> tuple[str, str]:
    """Retorna (código, texto explicativo)."""
    prof = prof or profile("")
    cycles = max(1, stats.cycles)
    is_wifi = mode in WIFI_MODES
    weak = (is_wifi and stats.wifi_min_signal is not None
            and stats.wifi_min_signal < weak_signal_pct)
    gw_bad = stats.gw_probed > 0 and stats.gw_fail >= stats.gw_probed / 2

    if is_wifi and stats.wifi_disconnected:
        return D_WIFI, ("Possível problema de Wi-Fi: o computador perdeu a conexão com a rede sem fio "
                        "durante a falha.")

    if stats.no_route >= cycles / 2:
        if is_wifi:
            return D_WIFI, ("Possível problema de Wi-Fi: o computador ficou sem rota de rede "
                            "(Wi-Fi desassociado) durante a falha.")
        return D_NO_ROUTE, ("O computador ficou sem rota de rede (cabo desconectado ou adaptador desligado). "
                            "A falha está no próprio computador ou na ligação dele com a rede.")

    if gw_bad:
        if weak:
            return D_WIFI, (f"Possível problema de Wi-Fi: sinal fraco ({stats.wifi_min_signal}%) e o roteador "
                            "também sem resposta no momento da falha.")
        return D_LOCAL, "Possível problema na rede local: o roteador também não respondeu durante a falha."

    if stats.gw_probed == 0:
        return D_UNKNOWN, ("Os destinos externos não responderam; não foi possível verificar o roteador "
                           "(gateway não identificado).")

    note = ""
    if weak:
        note = (f" Observação: o sinal Wi-Fi estava fraco ({stats.wifi_min_signal}%), mas o roteador respondeu, "
                "o que indica que a rede sem fio não era a causa principal.")

    if stats.dish_probed:
        equip = prof.equip_ref
        if stats.dish_ok >= stats.dish_probed / 2:
            return D_UPSTREAM, (f"Provável falha no acesso à internet: o roteador e {equip} responderam, "
                                f"mas não houve saída para a internet ({prof.upstream_text})." + note)
        return D_DISH_LINK, (f"Provável falha entre o roteador e {equip}: o roteador respondeu, "
                             f"mas {equip} e a internet não." + note)

    return D_INTERNET, ("Provável indisponibilidade da conexão com a internet: o roteador local respondeu "
                        "normalmente, mas nenhum destino externo respondeu." + note)
