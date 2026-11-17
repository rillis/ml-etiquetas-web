"""Alerta no Discord quando há etiquetas demais esperando.

Conta as vendas aguardando NF + as prontas para imprimir (fora as que estão num
processamento em andamento). Só age em dia útil (sem fim de semana e feriado nacional),
dentro da janela ALERTA_INICIO-ALERTA_FIM.

Anti-flood: pinga quando a fila passa de ALERTA_LIMITE; depois só pinga de novo se entrar
venda nova na fila ou após ALERTA_LEMBRETE_MIN sem resolver. Zera quando a fila volta ao limite.
"""
import logging
from datetime import datetime

import holidays

from app import config, discord, estado, processamento, vendas

log = logging.getLogger('alerta')
_FERIADOS = holidays.Brazil()


def na_janela(momento=None):
    momento = momento or estado.agora()
    return (momento.weekday() < 5 and momento.date() not in _FERIADOS
            and config.ALERTA_INICIO <= momento.time() < config.ALERTA_FIM)


def decidir(ids, anterior, momento):
    """Regra do ping. Devolve (pingar?, novo estado). ids: vendas pendentes agora."""
    hoje = momento.date().isoformat()
    if anterior.get('dia') != hoje:
        anterior = {}
    if len(ids) <= config.ALERTA_LIMITE:
        return False, {'dia': hoje, 'ativo': False, 'ids': [], 'ultimo_ping': None}
    if not anterior.get('ativo'):
        motivo = True
    else:
        novos = set(ids) - set(anterior.get('ids') or [])
        ultimo = datetime.fromisoformat(anterior['ultimo_ping'])
        motivo = bool(novos) or (momento - ultimo).total_seconds() >= config.ALERTA_LEMBRETE_MIN * 60
    if motivo:
        return True, {'dia': hoje, 'ativo': True, 'ids': sorted(ids), 'ultimo_ping': momento.isoformat()}
    return False, {**anterior, 'dia': hoje, 'ids': sorted(set(anterior.get('ids') or []) | set(ids))}


def verificar(ml):
    momento = estado.agora()
    if not config.ALERTA_ATIVO or not na_janela(momento):
        return
    job = processamento.em_andamento()
    em_lote = {v['venda'] for v in job['vendas']} if job else set()
    pendentes = [v for v in vendas.listar(ml) if v['venda'] not in em_lote]
    ids = [str(v['venda']) for v in pendentes]
    pingar, novo = decidir(ids, estado.ler('alerta', {}), momento)
    estado.gravar('alerta', novo)
    log.info('Alerta: %d pendente(s), pingar=%s', len(ids), pingar)
    if pingar:
        nf = sum(1 for v in pendentes if v['situacao'] == vendas.NF_PENDENTE)
        prontas = len(pendentes) - nf
        link = f'\n{config.SITE_URL}' if config.SITE_URL else ''
        discord.enviar(f'**{len(pendentes)} etiquetas pendentes:** {nf} aguardando NF, '
                       f'{prontas} prontas para imprimir{link}', pingar=True)
