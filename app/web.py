"""Site: lista as vendas, dispara o processamento e entrega os PDFs."""
import logging
import re
import threading
import time
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

from app import config, processamento, vendas
from app.ml import MLClient, MLError

log = logging.getLogger('web')
RE_PASTA = re.compile(r'^\d{8}-\d{4}\d{0,2}$')

_ml = MLClient()
_cache = {'quando': 0.0, 'vendas': None}
_cache_lock = threading.Lock()


def _listar(forcar=False):
    with _cache_lock:
        if forcar or _cache['vendas'] is None or time.time() - _cache['quando'] > config.CACHE_VENDAS_SEGUNDOS:
            _cache['vendas'] = vendas.listar(_ml)
            _cache['quando'] = time.time()
        return _cache['vendas'], _cache['quando']


def create_app():
    app = Flask(__name__)

    @app.after_request
    def _sem_cache(resp):
        if request.path.startswith('/api/'):
            resp.headers['Cache-Control'] = 'no-store'
        return resp

    def _exigir_json():
        if not request.is_json:
            abort(415)

    @app.get('/')
    def index():
        return render_template('index.html')

    @app.get('/api/vendas')
    def api_vendas():
        try:
            lista, quando = _listar(forcar=request.args.get('atualizar') == '1')
        except MLError as err:
            log.error('Falha ao listar vendas: %s', err)
            return jsonify({'erro': str(err)}), 502
        job = processamento.em_andamento()
        em_lote = {v['venda'] for v in job['vendas']} if job else set()
        return jsonify({
            'atualizado_em': quando,
            'vendas': [{**v, 'em_processamento': v['venda'] in em_lote} for v in lista],
        })

    @app.post('/api/processar')
    def api_processar():
        _exigir_json()
        pedidas = {str(x) for x in (request.get_json(silent=True) or {}).get('vendas') or []}
        if not pedidas:
            return jsonify({'erro': 'Nenhuma venda selecionada.'}), 400
        try:
            lista, _ = _listar(forcar=True)  # confere com a situação atual antes de emitir
        except MLError as err:
            return jsonify({'erro': str(err)}), 502
        pedidas_lista = [v for v in lista if str(v['venda']) in pedidas]
        escolhidas = [v for v in pedidas_lista if not v.get('sem_dados_fiscais')]
        if pedidas_lista and not escolhidas:
            return jsonify({'erro': 'Anúncio sem dados fiscais: corrija o anúncio no ML antes de emitir a NF.'}), 409
        if not escolhidas:
            return jsonify({'erro': 'As vendas selecionadas não estão mais pendentes. Atualize a lista.'}), 409
        try:
            job = processamento.criar(escolhidas)
        except processamento.LoteEmAndamento as err:
            return jsonify({'erro': str(err)}), 409
        return jsonify(job), 202

    @app.get('/api/job')
    def api_job():
        return jsonify(processamento.job_atual())

    @app.post('/api/job/visto')
    def api_job_visto():
        _exigir_json()
        processamento.marcar_visto()
        return jsonify({'ok': True})

    @app.get('/api/historico')
    def api_historico():
        return jsonify(processamento.historico())

    @app.get('/arquivos/<pasta>/<nome>')
    def arquivo(pasta, nome):
        if not RE_PASTA.match(pasta) or '/' in nome or nome.startswith('.') or not nome.endswith('.pdf'):
            abort(404)
        return send_from_directory(config.OUTPUT_DIR / pasta, nome, as_attachment=True)

    @app.get('/health')
    def health():
        try:
            idade = time.time() - Path(config.HEARTBEAT_FILE).stat().st_mtime
        except FileNotFoundError:
            idade = None
        ok = idade is not None and idade < 120
        return jsonify({'status': 'ok' if ok else 'worker_parado', 'heartbeat_s': idade}), 200 if ok else 503

    return app
