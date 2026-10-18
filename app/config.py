"""Configurações do sistema.

Ficam em data/config.json. Valores ausentes ou inválidos são substituídos
pelos padrões, então o arquivo pode ser editado à mão sem risco de travar
o programa.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from . import paths
from .providers import PROVIDERS

log = logging.getLogger(__name__)

CONNECTION_MODES = {
    "cabo": "Cabo direto",
    "wifi_direto": "Wi-Fi direto",
    "wifi_roteador": "Wi-Fi via roteador",
}
WIFI_MODES = {"wifi_direto", "wifi_roteador"}

# Os quatro "slots" de destino são fixos (cada um tem coluna própria no banco);
# o endereço de cada um é configurável.
TARGET_IDS = ("gw", "cf", "gg", "dish")
EXTERNAL_TARGETS = ("cf", "gg")


@dataclass
class TargetConfig:
    id: str
    name: str
    host: str          # "auto" = gateway padrão detectado
    tcp_port: int      # porta do teste TCP alternativo
    enabled: bool = True


def default_targets() -> list[TargetConfig]:
    return [
        TargetConfig("gw", "Roteador", "auto", 80),
        TargetConfig("cf", "Cloudflare", "1.1.1.1", 443),
        TargetConfig("gg", "Google", "8.8.8.8", 443),
        TargetConfig("dish", "Equipamento do provedor", "192.168.100.1", 80),
    ]


@dataclass
class Settings:
    port: int = 8000
    interval_ms: int = 1000
    timeout_ms: int = 800
    lat_normal_max_ms: float = 100.0
    lat_elevated_max_ms: float = 250.0
    loss_threshold_pct: float = 2.0
    loss_window_s: int = 60
    loss_min_cycles: int = 20
    latency_window_cycles: int = 30
    outage_confirm_cycles: int = 3
    wifi_weak_signal_pct: int = 40
    gap_threshold_s: float = 5.0
    retention_raw_days: int = 30
    retention_agg_days: int = 365
    prevent_sleep: bool = True
    autostart: bool = False
    dish_retry_minutes: int = 10
    dish_api_enabled: bool = True   # dados da própria antena Starlink (API local)
    chart_window_s: int = 180
    last_mode: str = "cabo"
    provider_type: str = ""     # "" = ainda não escolhido (o painel pergunta)
    provider_name: str = ""     # nome da operadora/plano, opcional (ex.: "Vivo Fibra 500 MB")
    targets: list[TargetConfig] = field(default_factory=default_targets)

    # ----- utilidades -----
    def target(self, tid: str) -> TargetConfig:
        for t in self.targets:
            if t.id == tid:
                return t
        raise KeyError(tid)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def public_dict(self) -> dict[str, Any]:
        d = self.to_dict()
        d["connection_modes"] = CONNECTION_MODES
        return d


_RANGES: dict[str, tuple[float, float]] = {
    "port": (1024, 65535),
    "interval_ms": (500, 60000),
    "timeout_ms": (100, 5000),
    "lat_normal_max_ms": (5, 5000),
    "lat_elevated_max_ms": (10, 10000),
    "loss_threshold_pct": (0, 100),
    "loss_window_s": (10, 3600),
    "loss_min_cycles": (1, 3600),
    "latency_window_cycles": (3, 3600),
    "outage_confirm_cycles": (1, 60),
    "wifi_weak_signal_pct": (1, 100),
    "gap_threshold_s": (2, 600),
    "retention_raw_days": (1, 3650),
    "retention_agg_days": (7, 3650),
    "dish_retry_minutes": (1, 1440),
    "chart_window_s": (30, 1800),
}


def _coerce(name: str, value: Any, default: Any) -> Any:
    try:
        if isinstance(default, bool):
            return bool(value)
        if isinstance(default, int):
            value = int(value)
        elif isinstance(default, float):
            value = float(value)
        elif isinstance(default, str):
            value = str(value)
        else:
            return default
    except (TypeError, ValueError):
        log.warning("Configuração inválida para %s: %r (usando %r)", name, value, default)
        return default
    if name in _RANGES:
        lo, hi = _RANGES[name]
        if not (lo <= value <= hi):
            log.warning("Configuração %s=%r fora do intervalo [%s, %s] (usando %r)", name, value, lo, hi, default)
            return default
    return value


def settings_from_dict(data: dict[str, Any]) -> Settings:
    base = Settings()
    for f in fields(Settings):
        if f.name == "targets" or f.name not in data:
            continue
        setattr(base, f.name, _coerce(f.name, data[f.name], getattr(base, f.name)))

    if base.last_mode not in CONNECTION_MODES:
        base.last_mode = "cabo"
    if base.provider_type and base.provider_type not in PROVIDERS:
        base.provider_type = ""
    base.provider_name = base.provider_name.strip()[:80]
    if base.timeout_ms >= base.interval_ms:
        base.timeout_ms = max(100, int(base.interval_ms * 0.8))
    if base.lat_elevated_max_ms <= base.lat_normal_max_ms:
        base.lat_elevated_max_ms = base.lat_normal_max_ms * 2.5

    raw_targets = data.get("targets")
    if isinstance(raw_targets, list):
        by_id = {t.id: t for t in base.targets}
        for raw in raw_targets:
            if not isinstance(raw, dict) or raw.get("id") not in by_id:
                continue
            t = by_id[raw["id"]]
            if isinstance(raw.get("host"), str):
                host = raw["host"].strip()
                if host or t.id == "dish":      # equipamento do provedor pode ficar sem endereço
                    t.host = host
            if isinstance(raw.get("name"), str) and raw["name"].strip():
                t.name = raw["name"].strip()
            try:
                port = int(raw.get("tcp_port", t.tcp_port))
                if 1 <= port <= 65535:
                    t.tcp_port = port
            except (TypeError, ValueError):
                pass
            if "enabled" in raw:
                t.enabled = bool(raw["enabled"])
        if not by_id["dish"].host:
            by_id["dish"].enabled = False
        # Pelo menos um destino externo precisa estar ativo.
        if not any(by_id[e].enabled for e in EXTERNAL_TARGETS):
            for e in EXTERNAL_TARGETS:
                by_id[e].enabled = True
    return base


class SettingsStore:
    """Carrega e salva as configurações com escrita atômica."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.CONFIG_FILE
        self._lock = threading.Lock()
        self.settings = self.load()

    def load(self) -> Settings:
        if not self.path.exists():
            s = Settings()
            self._write(s)
            return s
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("formato inesperado")
            migrated = False
            if "provider_type" not in data:
                # Configurações da versão 1.x (então chamada Starlink Monitor): a conexão era Starlink.
                data["provider_type"] = "starlink"
                migrated = True
            s = settings_from_dict(data)
            if migrated:
                self._write(s)
                log.info("Configurações migradas da versão 1.x (tipo de conexão: Starlink)")
            return s
        except Exception as exc:  # arquivo corrompido: guarda cópia e recomeça
            log.error("config.json ilegível (%s); usando padrões e guardando cópia .bak", exc)
            try:
                self.path.replace(self.path.with_suffix(".json.bak"))
            except OSError:
                pass
            s = Settings()
            self._write(s)
            return s

    def _write(self, s: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(s.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def save(self) -> None:
        with self._lock:
            self._write(self.settings)

    def update(self, **changes: Any) -> Settings:
        with self._lock:
            merged = self.settings.to_dict()
            merged.update(changes)
            self.settings = settings_from_dict(merged)
            self._write(self.settings)
            return self.settings
