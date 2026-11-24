"""Processamento de um lote de vendas: emite as NF-e, espera liberar as etiquetas e gera os PDFs.

Um lote por vez. O estado fica em data/estado/job.json, atualizado a cada passo, para o
site mostrar o progresso e para o worker continuar de onde parou se o container reiniciar.

Etapas de cada venda: fila -> conferindo -> emitindo -> autorizando -> aguardando_etiqueta -> pronta
(ou erro). Etapas do lote: fila -> processando -> gerando -> concluido.
"""
import logging
import shutil
import time
from datetime import datetime

from app import config, discord, estado, mensagem, pdfs
from app.ml import MLError

log = logging.getLogger('processamento')

ATIVOS = ('fila', 'processando', 'gerando')
LIBERADA = ('ready_to_print', 'printed')
ESPERANDO = ('emitindo', 'autorizando', 'aguardando_etiqueta')


class LoteEmAndamento(Exception):
    pass


def job_atual():
    return estado.ler('job')


def em_andamento():
    job = job_atual()
    return job if job and job.get('status') in ATIVOS else None


def criar(vendas):
    """Cria o lote (chamado pelo site). vendas: itens de vendas.listar()."""
    with estado.trava('job'):
        if em_andamento():
            raise LoteEmAndamento('Já existe um processamento em andamento.')
        job = {
            'id': estado.carimbo() + estado.agora().strftime('%S'),
            'status': 'fila',
            'criado_em': estado.agora().isoformat(),
            'aviso_demora': False,
            'visto': False,
            'vendas': [
                {**{k: v[k] for k in ('venda', 'pedidos', 'envio_id', 'comprador', 'apelido', 'itens', 'total')},
                 'etapa': 'fila', 'invoice_id': None, 'nf': None, 'erro': None}
                for v in vendas
            ],
        }
        estado.gravar('job', job)
        return job


def marcar_visto():
    with estado.trava('job'):
        job = job_atual()
        if job and job.get('status') == 'concluido':
            job['visto'] = True
            estado.gravar('job', job)


def _salvar(job):
    with estado.trava('job'):
        estado.gravar('job', job)


def _nota_existente(ml, v):
    for oid in v['pedidos']:
        nota = ml.get_or_none(f'/users/{ml.uid}/invoices/orders/{oid}')
        if nota and nota.get('status') not in ('canceled', 'rejected', 'error', 'denied'):
            return nota
    return None


def _emitir(ml, v):
    status, resp = ml.post_uma_vez(f'/users/{ml.uid}/invoices/orders', {'orders': v['pedidos']})
    if status < 400 and isinstance(resp, dict) and resp.get('id'):
        return resp['id'], None
    msg = resp.get('message') if isinstance(resp, dict) else str(resp)[:200]
    codigo = resp.get('error_code') if isinstance(resp, dict) else None
    if codigo:
        det = ml.get_or_none(f'/users/invoices/errors/MLB/{codigo}') or {}
        msg = det.get('display_message') or msg
    return None, f'Falha ao emitir a NF ({status}): {msg}'


def _conferir(ml, job, v):
    """Primeira passada: decide se a venda já está pronta, se precisa emitir ou se tem problema."""
    v['etapa'] = 'conferindo'
    _salvar(job)
    e = ml.envio(v['envio_id'])
    sub = e.get('substatus')
    nota = _nota_existente(ml, v)
    if nota:
        v['invoice_id'] = nota['id']
        v['nf'] = nota.get('invoice_number') or None
    if e.get('status') == 'ready_to_ship' and sub in LIBERADA:
        v['etapa'] = 'pronta'
        return
    if e.get('status') != 'ready_to_ship' or sub != 'invoice_pending':
        v['etapa'] = 'erro'
        v['erro'] = f"Envio em {e.get('status')}/{sub}: não dá para imprimir agora."
        return
    if not v['invoice_id']:
        v['etapa'] = 'emitindo'
        _salvar(job)
        inv_id, erro = _emitir(ml, v)
        if erro:
            v['etapa'] = 'erro'
            v['erro'] = erro
            return
        v['invoice_id'] = inv_id
    v['etapa'] = 'autorizando'


def _acompanhar(ml, v):
    nota = ml.get(f'/users/{ml.uid}/invoices/{v["invoice_id"]}')
    st = nota.get('status')
    if st == 'pending_authorization':
        v['etapa'] = 'autorizando'
        return
    if st != 'authorized':
        erros = '; '.join(x.get('description') or '' for x in nota.get('errors') or [] if x)
        v['etapa'] = 'erro'
        v['erro'] = f'NF {st}' + (f': {erros}' if erros else '') + '. Confira no painel do ML.'
        return
    v['nf'] = nota.get('invoice_number')
    e = ml.envio(v['envio_id'])
    v['etapa'] = 'pronta' if e.get('substatus') in LIBERADA else 'aguardando_etiqueta'


def _avisar_demora(job):
    faltam = [v for v in job['vendas'] if v['etapa'] in ESPERANDO]
    linhas = '\n'.join(f"- {v['comprador']} (venda {v['venda']}): {v['etapa'].replace('_', ' ')}" for v in faltam)
    discord.enviar(
        f'**Processamento esperando há mais de {config.AVISO_DEMORA_MIN} min** ({len(faltam)} venda(s))',
        titulo='Etiquetas ainda não liberadas', descricao=linhas, pingar=True)
    job['aviso_demora'] = True


def _gerar_saida(ml, job):
    prontas = [v for v in job['vendas'] if v['etapa'] == 'pronta']
    resultado = {'pasta': None, 'danfe': None, 'etiquetas': None, 'mensagem': None, 'avisos': [], 'estoque': None,
                 'erros': [{'venda': v['venda'], 'comprador': v['comprador'], 'erro': v['erro']}
                           for v in job['vendas'] if v['etapa'] == 'erro']}
    if not prontas:
        return resultado

    ts = estado.carimbo()
    pasta = config.OUTPUT_DIR / ts
    if pasta.exists():  # dois lotes no mesmo minuto
        ts = ts + estado.agora().strftime('%S')
        pasta = config.OUTPUT_DIR / ts
    pasta.mkdir(parents=True)

    for v in prontas:  # vendas que já vieram prontas: busca a nota para o DANFE
        if not v['invoice_id']:
            nota = _nota_existente(ml, v)
            if nota:
                v['invoice_id'], v['nf'] = nota['id'], nota.get('invoice_number')
            else:
                resultado['avisos'].append(f"{v['comprador']}: nota não encontrada, DANFE fora do PDF.")
    com_nota = [v['invoice_id'] for v in prontas if v['invoice_id']]
    if com_nota:
        resultado['danfe'] = f'{ts}_Danfe.pdf'
        pdfs.gerar_danfes(ml, com_nota, pasta / resultado['danfe'])
    resultado['etiquetas'], _ = pdfs.gerar_etiquetas(ml, [v['envio_id'] for v in prontas], pasta)

    texto, avisos, vendidos = mensagem.gerar(prontas)
    resultado.update(pasta=ts, mensagem=texto, estoque=mensagem.estoque_para(vendidos))
    resultado['avisos'] += avisos
    estado.gravar_json(pasta / 'resultado.json', {
        'id': ts, 'criado_em': estado.agora().isoformat(), **resultado,
        'vendas': [{k: v[k] for k in ('venda', 'comprador', 'nf', 'itens', 'total')} for v in prontas],
    })
    return resultado


def rodar(ml):
    """Executa (ou retoma) o lote atual até o fim. Chamado pelo worker numa thread."""
    job = em_andamento()
    if not job:
        return
    log.info('Processando lote %s (%d venda(s))', job['id'], len(job['vendas']))
    job['status'] = 'processando'
    job.setdefault('iniciado_em', estado.agora().isoformat())
    _salvar(job)

    for v in job['vendas']:
        if v['etapa'] in ('fila', 'conferindo', 'emitindo'):
            try:
                _conferir(ml, job, v)
            except MLError as err:
                v['etapa'] = 'erro'
                v['erro'] = str(err)
            _salvar(job)

    inicio = datetime.fromisoformat(job['iniciado_em'])
    while any(v['etapa'] in ESPERANDO for v in job['vendas']):
        time.sleep(config.INTERVALO_CONSULTA)
        for v in job['vendas']:
            if v['etapa'] in ESPERANDO:
                try:
                    _acompanhar(ml, v)
                except MLError as err:  # falha passageira: tenta de novo na próxima volta
                    log.warning('Venda %s: %s', v['venda'], err)
        if not job['aviso_demora'] and (estado.agora() - inicio).total_seconds() > config.AVISO_DEMORA_MIN * 60:
            _avisar_demora(job)
        _salvar(job)

    job['status'] = 'gerando'
    _salvar(job)
    try:
        job['resultado'] = _gerar_saida(ml, job)
    except Exception as err:
        log.exception('Falha ao gerar os PDFs')
        job['resultado'] = {'erros': [{'venda': None, 'comprador': None, 'erro': f'Falha ao gerar os PDFs: {err}'}]}
    job['status'] = 'concluido'
    job['concluido_em'] = estado.agora().isoformat()
    _salvar(job)
    log.info('Lote %s concluído', job['id'])


def marcar_baixa(pasta, chave, feito):
    """Anota no resultado do lote que um item do estoque de devoluções já foi baixado
    (a baixa em si é feita pelo módulo Estoque do ml-app). Devolve a lista atualizada."""
    caminho = config.OUTPUT_DIR / pasta / 'resultado.json'
    with estado.trava('baixas'):
        r = estado.ler_json(caminho)
        if r is None:
            return None
        baixas = [b for b in r.get('baixas') or [] if b != chave]
        if feito:
            baixas.append(chave)
        r['baixas'] = baixas
        estado.gravar_json(caminho, r)
    return baixas


def baixas_de(pasta):
    r = estado.ler_json(config.OUTPUT_DIR / pasta / 'resultado.json') or {}
    return r.get('baixas') or []


def historico():
    itens = []
    if config.OUTPUT_DIR.exists():
        for pasta in sorted(config.OUTPUT_DIR.iterdir(), reverse=True):
            r = estado.ler_json(pasta / 'resultado.json')
            if r:
                itens.append(r)
    return itens


def limpar_historico():
    limite = time.time() - config.HISTORICO_DIAS * 86400
    if not config.OUTPUT_DIR.exists():
        return
    for pasta in config.OUTPUT_DIR.iterdir():
        if pasta.is_dir() and pasta.stat().st_mtime < limite:
            shutil.rmtree(pasta, ignore_errors=True)
            log.info('Histórico apagado: %s', pasta.name)
