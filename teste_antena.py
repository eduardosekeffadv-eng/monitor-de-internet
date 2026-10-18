"""Teste de leitura da antena Starlink (somente leitura).

Pergunta à antena, pela rede local (192.168.100.1, porta 9200), quais dados
ela informa: estado, obstruções, alertas, latência, perda e histórico de
quedas com o motivo. NÃO altera nada na antena: nenhum comando de reinício,
recolhimento (stow) ou configuração é usado.

O resultado é salvo em teste_antena_resultado.txt, na mesma pasta.
O identificador da antena é mascarado e a localização não é consultada.
"""

from __future__ import annotations

import datetime as dt
import platform
import socket
import sys
import time
import traceback
from pathlib import Path

HOST = "192.168.100.1"
PORT = 9200
OUT = Path(__file__).with_name("teste_antena_resultado.txt")
lines: list[str] = []


def out(text: str = "") -> None:
    print(text)
    lines.append(text)


def section(title: str) -> None:
    out()
    out("=" * 64)
    out(title)
    out("=" * 64)


def fmt_dur(s) -> str:
    try:
        s = int(s)
    except (TypeError, ValueError):
        return "—"
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return (f"{d} d " if d else "") + (f"{h} h " if h or d else "") + f"{m} min {s} s"


def pct(v) -> str:
    try:
        return f"{float(v) * 100:.2f}%".replace(".", ",")
    except (TypeError, ValueError):
        return "—"


def main() -> int:
    out("TESTE DE LEITURA DA ANTENA STARLINK — Monitor de Internet")
    out(f"Data: {dt.datetime.now():%d/%m/%Y %H:%M:%S}   Python {platform.python_version()}   {platform.system()} {platform.release()}")

    # 1. A porta de diagnóstico da antena está acessível?
    section("1. Acesso à antena pela rede")
    t0 = time.perf_counter()
    try:
        with socket.create_connection((HOST, PORT), timeout=3):
            pass
        out(f"OK: {HOST}:{PORT} respondeu em {(time.perf_counter() - t0) * 1000:.0f} ms.")
    except OSError as exc:
        out(f"FALHOU: não foi possível conectar em {HOST}:{PORT} ({exc}).")
        out("A antena não está acessível por esta rede para consulta de dados.")
        out("Com roteador próprio, isso pode depender da configuração dele.")
        return 2

    try:
        import starlink_grpc
    except ImportError as exc:
        out(f"FALHOU: biblioteca não instalada ({exc}). Rode o testar_antena.bat.")
        return 3
    starlink_grpc.REQUEST_TIMEOUT = 8
    ctx = starlink_grpc.ChannelContext(target=f"{HOST}:{PORT}")
    ok_parts = []

    # 2. Situação atual
    section("2. Situação atual da antena")
    try:
        status, obstruction, alerts = starlink_grpc.status_data(ctx)
        did = str(status.get("id") or "")
        masked = (did[:6] + "…" + did[-4:]) if len(did) > 12 else "(oculto)"
        out(f"Identificador (mascarado): {masked}")
        out(f"Hardware: {status.get('hardware_version')}   Software: {status.get('software_version')}")
        out(f"Estado: {status.get('state')}   (CONNECTED = conectada)")
        out(f"Ligada há: {fmt_dur(status.get('uptime'))}")
        out(f"Latência até a rede da Starlink (PoP): {status.get('pop_ping_latency_ms')} ms")
        out(f"Perda de pacotes até a rede da Starlink: {pct(status.get('pop_ping_drop_rate'))}")
        out(f"Tráfego agora: download {round((status.get('downlink_throughput_bps') or 0) / 1e6, 2)} Mbps · "
            f"upload {round((status.get('uplink_throughput_bps') or 0) / 1e6, 2)} Mbps")
        out(f"Obstruída neste momento: {obstruction.get('currently_obstructed')}")
        out(f"Fração do céu obstruída: {pct(obstruction.get('fraction_obstructed'))}")
        out(f"Obstruções prolongadas: duração média {obstruction.get('avg_prolonged_obstruction_duration')} s, "
            f"intervalo médio {obstruction.get('avg_prolonged_obstruction_interval')} s")
        active = [k.replace("alert_", "") for k, v in alerts.items() if v]
        out(f"Alertas ativos: {', '.join(active) if active else 'nenhum'}")
        out(f"Tipos de alerta que esta antena informa: {', '.join(k.replace('alert_', '') for k in alerts)}")
        ok_parts.append("situação atual")
    except Exception as exc:
        out(f"FALHOU: {exc}")
        out(traceback.format_exc(limit=2))

    # 3. Estatísticas dos últimos 15 minutos
    section("3. Estatísticas dos últimos 15 minutos (medidas pela antena)")
    try:
        general, drop, drop_rl, latency, loaded, usage, power = starlink_grpc.history_stats(900, context=ctx)
        out(f"Amostras analisadas: {general.get('samples')}")
        out(f"Perda total (equivalente em segundos): {drop.get('total_ping_drop')}")
        out(f"Segundos com 100% de perda: {drop.get('count_full_ping_drop')}")
        out(f"Segundos com perda por obstrução: {drop.get('count_obstructed')} (100%: {drop.get('count_full_obstructed_ping_drop')})")
        out(f"Segundos com perda por falta de satélite: {drop.get('count_unscheduled')} (100%: {drop.get('count_full_unscheduled_ping_drop')})")
        runs_s = drop_rl.get("run_seconds[1,]") or []
        runs_m = drop_rl.get("run_minutes[1,]") or []
        longest = max((i + 1 for i, n in enumerate(runs_s) if n), default=0)
        if any(runs_m):
            longest = max(longest, max(60 * (i + 1) for i, n in enumerate(runs_m) if n))
        out(f"Sequências sem conexão: {sum(runs_s) + sum(runs_m)}   a maior: ~{longest} s")
        dec = latency.get("deciles_all_ping_latency[]") or []
        med = f"{dec[5]:.0f}" if len(dec) > 5 and dec[5] is not None else "—"
        mean = latency.get("mean_all_ping_latency")
        out(f"Latência mediana: {med} ms   média: {'—' if mean is None else f'{mean:.0f}'} ms")
        if power:
            out(f"Consumo médio: {power.get('mean_power')} W")
        ok_parts.append("estatísticas")
    except Exception as exc:
        out(f"FALHOU: {exc}")

    # 4. Histórico de quedas com o motivo
    section("4. Quedas registradas pela antena (com o motivo)")
    try:
        hist = starlink_grpc.get_history(ctx)
        outages = list(getattr(hist, "outages", []) or [])
        if not outages:
            out("A antena não informou lista de quedas (campo ausente ou vazio).")
        else:
            out(f"A antena informou {len(outages)} queda(s) recente(s). Últimas 25:")
            def cause_name(o):
                try:
                    enum = o.DESCRIPTOR.fields_by_name["cause"].enum_type
                    return enum.values_by_number[o.cause].name
                except Exception:
                    return str(getattr(o, "cause", "?"))
            gps_epoch = dt.datetime(1980, 1, 6, tzinfo=dt.timezone.utc)
            for o in outages[-25:]:
                cause = cause_name(o)
                start_ns = int(getattr(o, "start_timestamp_ns", 0) or 0)
                dur_s = int(getattr(o, "duration_ns", 0) or 0) / 1e9
                # o horário vem em tempo GPS (desde 06/01/1980, sem segundos bissextos)
                when = gps_epoch + dt.timedelta(seconds=start_ns / 1e9 - 18)
                local = when.astimezone()
                out(f"  {local:%d/%m %H:%M:%S}  duração {dur_s:6.1f} s  motivo: {cause}"
                    f"{'  (trocou de satélite)' if getattr(o, 'did_switch', False) else ''}")
            ok_parts.append("histórico de quedas")
    except Exception as exc:
        out(f"FALHOU: {exc}")

    # 5. Mapa de obstruções (só verifica se existe)
    section("5. Mapa de obstruções")
    try:
        m = starlink_grpc.obstruction_map(ctx)
        rows = len(m)
        cols = len(m[0]) if rows else 0
        out(f"Disponível: {rows} × {cols} pontos.")
        ok_parts.append("mapa de obstruções")
    except Exception as exc:
        out(f"Indisponível: {exc}")

    section("RESUMO")
    out(f"Dados obtidos: {', '.join(ok_parts) if ok_parts else 'nenhum'}")
    out("Envie este arquivo (teste_antena_resultado.txt) para análise.")
    return 0 if ok_parts else 1


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception:
        out("ERRO INESPERADO:")
        out(traceback.format_exc())
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nResultado salvo em: {OUT}")
    sys.exit(code)
