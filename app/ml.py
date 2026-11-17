"""Cliente da API do Mercado Livre.

O token vem do tokens.json do ml-jrf (só leitura). Num 401, chama o ping do ml-jrf
e relê o arquivo uma vez; este serviço nunca renova o token.
"""
import json
import logging
import threading
import time

import requests

from app import config

log = logging.getLogger('ml')


class MLError(Exception):
    def __init__(self, status, url, corpo=''):
        super().__init__(f'ML {status} em {url}: {str(corpo)[:300]}')
        self.status = status


class MLClient:
    TENTATIVAS = 3

    def __init__(self):
        self.session = requests.Session()
        self._uid = None
        self._lock = threading.Lock()

    def _token(self):
        with open(config.TOKENS_FILE, encoding='utf-8') as f:
            return json.load(f).get('token', '')

    def _ping(self):
        if not config.ML_JRF_PING_URL:
            raise MLError(401, 'token', 'ML_JRF_PING_URL não configurado')
        try:
            r = self.session.post(config.ML_JRF_PING_URL, timeout=30)
        except requests.RequestException as err:
            raise MLError('rede', config.ML_JRF_PING_URL, err) from err
        if r.status_code == 503:
            raise MLError(401, 'token', 'token recusado pelo ML: reautorize o ml-jrf')
        if r.status_code != 200:
            raise MLError(r.status_code, config.ML_JRF_PING_URL, r.text)

    def _request(self, metodo, path, repetir=True, **kw):
        url = path if path.startswith('http') else config.ML_API_URL + path
        pingou = False
        tentativa = 0
        while True:
            tentativa += 1
            headers = {'Authorization': 'Bearer ' + self._token(), **kw.pop('headers', {})}
            try:
                r = self.session.request(metodo, url, headers=headers, timeout=60, **kw)
            except requests.RequestException as err:
                if not repetir or tentativa >= self.TENTATIVAS:
                    raise MLError('rede', url, err) from err
                time.sleep(tentativa * 2)
                continue
            if r.status_code == 401 and not pingou:
                self._ping()
                pingou = True
                tentativa -= 1
                continue
            if repetir and (r.status_code == 429 or r.status_code >= 500) and tentativa < self.TENTATIVAS:
                time.sleep(tentativa * 2)
                continue
            return r

    def get(self, path, params=None, headers=None):
        r = self._request('GET', path, params=params, headers=headers or {})
        if r.status_code >= 400:
            raise MLError(r.status_code, path, r.text)
        return r.json()

    def get_or_none(self, path, params=None, headers=None):
        try:
            return self.get(path, params=params, headers=headers)
        except MLError as err:
            if err.status == 404:
                return None
            raise

    def get_pdf(self, path, params=None):
        """GET que devolve PDF. Retorna (bytes, nome sugerido pelo ML ou None)."""
        r = self._request('GET', path, params=params, headers={})
        if r.status_code != 200 or not r.content.startswith(b'%PDF'):
            raise MLError(r.status_code, path, r.text)
        disp = r.headers.get('content-disposition', '')
        nome = disp.split('filename=')[-1].strip('"; ') if 'filename=' in disp else None
        return r.content, nome

    def post_uma_vez(self, path, corpo):
        """POST sem nova tentativa (emitir NF duas vezes seria pior que falhar).

        Devolve (status, json ou texto).
        """
        r = self._request('POST', path, repetir=False, json=corpo, headers={})
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text

    def envio(self, envio_id):
        return self.get(f'/shipments/{envio_id}', headers={'x-format-new': 'true'})

    @property
    def uid(self):
        with self._lock:
            if self._uid is None:
                self._uid = self.get('/users/me')['id']
            return self._uid
