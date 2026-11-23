"""Vendas que ainda precisam passar pelo site: aguardando NF ou prontas para imprimir."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from app import config, estado

NF_PENDENTE = 'nf_pendente'
PRONTA = 'pronta'
SUBSTATUS = {'invoice_pending': NF_PENDENTE, 'ready_to_print': PRONTA}
PAGINA = 50
# Envios nesses estados não voltam a ficar pendentes: não precisa consultar de novo.
FINAIS = {'shipped', 'delivered', 'not_delivered', 'cancelled'}
_finais = set()
# Anúncios que o Faturador aceita (com dados fiscais). Só guarda os positivos: um anúncio
# sem dados fiscais volta a ser consultado até alguém corrigir.
_faturaveis = set()


def _pedidos_pagos(ml, dias):
    desde = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime('%Y-%m-%dT%H:%M:%S.000-00:00')
    offset = 0
    while True:
        r = ml.get('/orders/search', params={
            'seller': ml.uid, 'order.status': 'paid', 'order.date_created.from': desde,
            'sort': 'date_desc', 'limit': PAGINA, 'offset': offset,
        })
        yield from r['results']
        offset += PAGINA
        if offset >= r['paging']['total']:
            break


def nome_destinatario(envio, pedido):
    nome = ((envio.get('destination') or {}).get('receiver_name') or '').strip()
    if not nome:
        b = pedido.get('buyer') or {}
        nome = f"{b.get('first_name') or ''} {b.get('last_name') or ''}".strip() or b.get('nickname') or ''
    return nome.title()


def _sem_dados_fiscais(ml, item_ids):
    """Dos anúncios informados, os que o ML recusa para NF-e (sem dados fiscais)."""
    a_consultar = [i for i in set(item_ids) if i not in _faturaveis]

    def recusado(item_id):
        try:
            r = ml.get_or_none(f'/can_invoice/items/{item_id}') or {}
        except Exception:  # na dúvida não trava a venda; a emissão mostra o erro
            return False
        if r.get('status') is False:
            return True
        _faturaveis.add(item_id)
        return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        return {i for i, ruim in zip(a_consultar, pool.map(recusado, a_consultar)) if ruim}


def ja_processadas():
    """Vendas que já saíram com etiqueta em algum lote do histórico. O ML às vezes demora
    para marcar o envio como impresso, e elas não devem voltar para a lista nesse meio-tempo."""
    ids = set()
    if config.OUTPUT_DIR.exists():
        for pasta in config.OUTPUT_DIR.iterdir():
            r = estado.ler_json(pasta / 'resultado.json') or {}
            ids.update(str(v['venda']) for v in r.get('vendas') or [])
    return ids


def listar(ml, dias=None):
    """Lista as vendas aguardando NF (invoice_pending) ou prontas para imprimir (ready_to_print),
    menos as que já foram processadas."""
    processadas = ja_processadas()
    por_envio = {}
    for pedido in _pedidos_pagos(ml, dias or config.DIAS_BUSCA):
        envio_id = (pedido.get('shipping') or {}).get('id')
        if envio_id:
            por_envio.setdefault(envio_id, []).append(pedido)

    a_consultar = [i for i in por_envio if i not in _finais]
    with ThreadPoolExecutor(max_workers=8) as pool:
        envios = dict(zip(a_consultar, pool.map(ml.envio, a_consultar)))
    for envio_id, e in envios.items():
        if e.get('status') in FINAIS:
            _finais.add(envio_id)

    vendas = []
    for envio_id, pedidos in por_envio.items():
        if envio_id in _finais:
            continue
        e = envios[envio_id]
        situacao = SUBSTATUS.get(e.get('substatus'))
        if e.get('status') != 'ready_to_ship' or not situacao:
            continue
        if (e.get('logistic') or {}).get('type') == 'fulfillment':
            continue
        p0 = pedidos[0]
        if str(p0.get('pack_id') or p0['id']) in processadas:
            continue
        entrega = ((e.get('lead_time') or {}).get('estimated_delivery_time') or {}).get('date')
        vendas.append({
            'venda': p0.get('pack_id') or p0['id'],
            'pedidos': [p['id'] for p in pedidos],
            'envio_id': envio_id,
            'situacao': situacao,
            'data': p0['date_created'],
            'entrega': entrega,
            'comprador': nome_destinatario(e, p0),
            'apelido': (p0.get('buyer') or {}).get('nickname'),
            'total': round(sum(p.get('total_amount') or 0 for p in pedidos), 2),
            'b2b': any('b2b' in (p.get('tags') or []) for p in pedidos),
            'itens': [
                {
                    'item_id': it['item']['id'],
                    'sku': it['item'].get('seller_sku') or '',
                    'titulo': it['item'].get('title'),
                    'qtd': it['quantity'],
                }
                for p in pedidos for it in p['order_items']
            ],
        })
    pend_nf = [v for v in vendas if v['situacao'] == NF_PENDENTE]
    ruins = _sem_dados_fiscais(ml, [it['item_id'] for v in pend_nf for it in v['itens']])
    for v in vendas:
        v['sem_dados_fiscais'] = v['situacao'] == NF_PENDENTE and any(it['item_id'] in ruins for it in v['itens'])
    vendas.sort(key=lambda v: v['data'])
    return vendas
