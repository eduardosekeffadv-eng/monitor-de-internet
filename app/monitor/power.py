"""Impede a suspensão automática do Windows enquanto o monitor roda.

Usa SetThreadExecutionState (não exige administrador). O efeito vale
enquanto a thread que fez a chamada existir, então ela é feita pela thread
principal do servidor e renovada periodicamente.

Limitação: isso impede apenas a suspensão por INATIVIDADE. Fechar a tampa
do notebook, apertar o botão de energia ou escolher "Suspender" no menu
ainda suspendem o computador — e esses períodos são registrados como
"monitoramento inativo", nunca como queda da conexão. A tela pode apagar
normalmente.
"""

from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def set_prevent_sleep(enabled: bool) -> bool:
    if sys.platform != "win32":
        return False
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if enabled else 0)
    try:
        prev = ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags))
    except Exception as exc:  # pragma: no cover
        log.warning("SetThreadExecutionState falhou: %s", exc)
        return False
    return prev != 0
