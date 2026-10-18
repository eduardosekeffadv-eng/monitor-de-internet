"""Detector: perda de pacotes, microquedas, interrupções, restabelecimento e diagnóstico."""

from app.events.detector import E_LATENCY, E_LOSS, E_MICRO, E_OUTAGE, Detector
from app.monitor.types import INSTAVEL, OFFLINE, ONLINE, S_FAIL, S_OK, S_PARTIAL
from conftest import FAIL, T0, cycle


def run(det, cycles):
    outs = []
    for c in cycles:
        outs.append(det.process(c))
    return outs


def changes(outs):
    return [(a, e.type) for o in outs for a, e in o.changes]


def test_conexao_normal_sem_eventos(settings):
    det = Detector(settings)
    outs = run(det, [cycle(i) for i in range(30)])
    assert all(o.conn_status == ONLINE for o in outs)
    assert all(o.sample_status == S_OK for o in outs)
    assert changes(outs) == []
    assert outs[-1].primary_ms == 30


def test_falha_de_um_unico_servidor_nao_e_queda(settings):
    det = Detector(settings)
    outs = run(det, [cycle(i, cf=FAIL) for i in range(120)])
    assert all(o.conn_status != OFFLINE for o in outs)
    assert all(e_type != E_OUTAGE for _, e_type in changes(outs))
    assert outs[0].sample_status == S_PARTIAL
    # Depois de 60 ciclos sem resposta, o servidor é considerado fora do ar e deixa de contar como perda
    assert "cf" in outs[-1].dead_targets
    assert outs[-1].conn_status == ONLINE
    assert outs[-1].loss_pct == 0


def test_microqueda_registrada_com_inicio_e_fim(settings):
    det = Detector(settings)
    seq = [cycle(i) for i in range(10)]
    seq += [cycle(10, cf=FAIL, gg=FAIL), cycle(11, cf=FAIL, gg=FAIL)]
    seq += [cycle(12)]
    outs = run(det, seq)
    assert outs[10].conn_status == INSTAVEL and outs[10].sample_status == S_FAIL
    micro = [e for o in outs for a, e in o.changes if e.type == E_MICRO]
    assert len(micro) == 1
    m = micro[0]
    assert m.start_ts == T0 + 10_000 and m.end_ts == T0 + 12_000
    assert m.duration_ms == 2000 and m.precision_ms == 1000
    assert m.diagnosis == "internet"
    assert not any(t == E_OUTAGE for _, t in changes(outs))


def test_interrupcao_confirmada_retroativa_e_restabelecimento(settings):
    det = Detector(settings)
    seq = [cycle(i, cf=40, gg=40) for i in range(10)]
    seq += [cycle(i, cf=FAIL, gg=FAIL) for i in range(10, 18)]   # 8 s sem resposta
    seq += [cycle(i, cf=50, gg=50) for i in range(18, 25)]
    outs = run(det, seq)

    assert outs[11].conn_status == INSTAVEL          # 2 ciclos: ainda verificando
    assert outs[12].conn_status == OFFLINE           # 3º ciclo: confirmada
    opened = [e for a, e in outs[12].changes if a == "open" and e.type == E_OUTAGE]
    assert opened and opened[0].start_ts == T0 + 10_000   # início retroativo
    assert opened[0].lat_before == 40

    closed = [e for a, e in outs[18].changes if a == "close" and e.type == E_OUTAGE]
    assert closed
    ev = closed[0]
    assert ev.end_ts == T0 + 18_000 and ev.duration_ms == 8000
    assert ev.router_state == "respondendo normalmente"
    assert ev.targets["cf"] == "sem resposta" and ev.targets["gw"] == "respondendo"
    assert ev.diagnosis == "internet"
    assert outs[18].conn_status != OFFLINE

    # latência depois: média dos 5 primeiros ciclos após a volta
    updates = [e for o in outs[18:] for a, e in o.changes if a == "update" and e.type == E_OUTAGE]
    assert updates and updates[-1].lat_after == 50

    # Os ciclos da interrupção não viram "perda de pacotes"
    assert not any(t == E_LOSS for _, t in changes(outs))


def test_roteador_sem_resposta_indica_rede_local(settings):
    det = Detector(settings)
    seq = [cycle(i) for i in range(5)]
    seq += [cycle(i, cf=FAIL, gg=FAIL, gw=FAIL) for i in range(5, 10)]
    seq += [cycle(10)]
    outs = run(det, seq)
    ev = [e for a, e in outs[10].changes if e.type == E_OUTAGE][0]
    assert ev.diagnosis == "rede_local"
    assert ev.router_state == "sem resposta"


def test_sem_rota_de_rede(settings):
    det = Detector(settings)
    seq = [cycle(i) for i in range(3)]
    seq += [cycle(i, cf=FAIL, gg=FAIL, gw=FAIL, gateway=None) for i in range(3, 8)]
    seq += [cycle(8)]
    outs = run(det, seq)
    ev = [e for a, e in outs[8].changes if e.type == E_OUTAGE][0]
    assert ev.diagnosis == "sem_rota"


def test_wifi_fraco_com_roteador_sem_resposta(settings):
    det = Detector(settings)
    seq = [cycle(i, mode="wifi_roteador", wifi_signal=70) for i in range(3)]
    seq += [cycle(i, cf=FAIL, gg=FAIL, gw=FAIL, mode="wifi_roteador", wifi_signal=22) for i in range(3, 8)]
    seq += [cycle(8, mode="wifi_roteador", wifi_signal=60)]
    outs = run(det, seq)
    ev = [e for a, e in outs[8].changes if e.type == E_OUTAGE][0]
    assert ev.diagnosis == "wifi"
    assert ev.wifi_signal == 22


def test_wifi_fraco_mas_roteador_ok_nao_culpa_o_wifi(settings):
    det = Detector(settings)
    seq = [cycle(i, mode="wifi_direto", wifi_signal=70) for i in range(3)]
    seq += [cycle(i, cf=FAIL, gg=FAIL, mode="wifi_direto", wifi_signal=30) for i in range(3, 8)]
    seq += [cycle(8, mode="wifi_direto", wifi_signal=60)]
    outs = run(det, seq)
    ev = [e for a, e in outs[8].changes if e.type == E_OUTAGE][0]
    assert ev.diagnosis == "internet"
    assert "sinal Wi-Fi estava fraco" in ev.diagnosis_text


def test_antena_respondendo_durante_falha(settings):
    det = Detector(settings)
    seq = [cycle(i, dish=1) for i in range(3)]
    seq += [cycle(i, cf=FAIL, gg=FAIL, dish=1) for i in range(3, 8)]
    seq += [cycle(8, dish=1)]
    outs = run(det, seq)
    ev = [e for a, e in outs[8].changes if e.type == E_OUTAGE][0]
    assert ev.diagnosis == "internet_antena_ok"


def test_perda_de_pacotes_gera_instabilidade_e_normaliza(settings):
    det = Detector(settings)
    seq = [cycle(i) for i in range(30)]
    # ~5% de perda: um destino falha em 1 de cada 10 ciclos
    seq += [cycle(i, cf=FAIL if i % 10 == 0 else 30) for i in range(30, 90)]
    seq += [cycle(i) for i in range(90, 200)]
    outs = run(det, seq)
    ch = changes(outs)
    assert ("open", E_LOSS) in ch and ("close", E_LOSS) in ch
    assert any(o.conn_status == INSTAVEL for o in outs[30:90])
    assert outs[-1].conn_status == ONLINE


def test_latencia_elevada(settings):
    det = Detector(settings)
    seq = [cycle(i) for i in range(20)]
    seq += [cycle(i, cf=180, gg=190) for i in range(20, 60)]
    seq += [cycle(i) for i in range(60, 100)]
    outs = run(det, seq)
    ch = changes(outs)
    assert ("open", E_LATENCY) in ch and ("close", E_LATENCY) in ch
    lat = [e for o in outs for a, e in o.changes if e.type == E_LATENCY and a == "close"][0]
    assert lat.details["pico_ms"] == 180   # latência principal = menor entre os externos


def test_interrupcao_encerrada_quando_monitor_para(settings):
    det = Detector(settings)
    run(det, [cycle(i) for i in range(3)] + [cycle(i, cf=FAIL, gg=FAIL) for i in range(3, 9)])
    assert det.outage is not None
    ch = det.interrupt(T0 + 8_000, "computador suspenso")
    ev = [e for a, e in ch if e.type == E_OUTAGE][0]
    assert ev.truncated and ev.end_ts == T0 + 8_000
    assert "computador suspenso" in ev.details["observacao"]
    assert det.outage is None and det.fail_run == 0


def test_criterio_configuravel_de_confirmacao(settings):
    settings.outage_confirm_cycles = 5
    det = Detector(settings)
    outs = run(det, [cycle(0)] + [cycle(i, cf=FAIL, gg=FAIL) for i in range(1, 5)] + [cycle(5)])
    ch = changes(outs)
    assert ("instant", E_MICRO) in ch and ("open", E_OUTAGE) not in ch
