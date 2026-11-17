"""Worker: roda os processamentos (numa thread), o alerta periódico e a limpeza do histórico."""
import logging
import threading
import time
from pathlib import Path

from app import alerta, config, processamento
from app.ml import MLClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('worker')

POLL = 2


def _thread_lote(ml):
    try:
        processamento.rodar(ml)
    except Exception:
        log.exception('Falha no processamento; tenta retomar na próxima volta')
        time.sleep(30)


def main():
    ml = MLClient()
    lote = None
    prox_alerta = 0.0
    prox_limpeza = 0.0
    log.info('Worker iniciado')
    while True:
        Path(config.HEARTBEAT_FILE).touch()
        agora = time.time()

        if (lote is None or not lote.is_alive()) and processamento.em_andamento():
            lote = threading.Thread(target=_thread_lote, args=(ml,), daemon=True, name='lote')
            lote.start()

        if agora >= prox_alerta:
            prox_alerta = agora + config.ALERTA_INTERVALO_MIN * 60
            try:
                alerta.verificar(ml)
            except Exception:
                log.exception('Falha no alerta')

        if agora >= prox_limpeza:
            prox_limpeza = agora + 3600
            processamento.limpar_historico()

        time.sleep(POLL)


if __name__ == '__main__':
    main()
