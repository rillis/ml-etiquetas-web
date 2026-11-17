"""Configuração lida do ambiente (.env via docker compose)."""
import os
from datetime import time
from pathlib import Path


def _env(nome, padrao=None, tipo=str):
    valor = os.getenv(nome)
    if valor is None or valor == '':
        return padrao
    return tipo(valor)


def _hora(texto):
    h, m = texto.split(':')
    return time(int(h), int(m))


BASE = Path(__file__).resolve().parent.parent
DATA_DIR = Path(_env('DATA_DIR', str(BASE / 'data')))
ESTADO_DIR = DATA_DIR / 'estado'
OUTPUT_DIR = DATA_DIR / 'output'
TIMEZONE = 'America/Sao_Paulo'

# Mercado Livre (token do ml-jrf, só leitura)
ML_API_URL = 'https://api.mercadolibre.com'
TOKENS_FILE = _env('TOKENS_FILE', '/app/mljrf/tokens.json')
ML_JRF_PING_URL = _env('ML_JRF_PING_URL', '')

# Vendas
DIAS_BUSCA = _env('DIAS_BUSCA', 15, int)                    # dias para trás na busca de pedidos
CACHE_VENDAS_SEGUNDOS = _env('CACHE_VENDAS_SEGUNDOS', 30, int)

# Processamento
INTERVALO_CONSULTA = _env('INTERVALO_CONSULTA', 20, int)    # segundos entre consultas de NF/etiqueta
AVISO_DEMORA_MIN = _env('AVISO_DEMORA_MIN', 30, int)        # avisa no Discord se passar disso
HISTORICO_DIAS = _env('HISTORICO_DIAS', 30, int)

# Alerta
ALERTA_ATIVO = _env('ALERTA_ATIVO', '1') == '1'
ALERTA_INICIO = _hora(_env('ALERTA_INICIO', '09:30'))
ALERTA_FIM = _hora(_env('ALERTA_FIM', '16:00'))
ALERTA_INTERVALO_MIN = _env('ALERTA_INTERVALO_MIN', 10, int)
ALERTA_LIMITE = _env('ALERTA_LIMITE', 2, int)               # pinga quando passar disso
ALERTA_LEMBRETE_MIN = _env('ALERTA_LEMBRETE_MIN', 120, int)

# Discord
DISCORD_WEBHOOK_URL = _env('DISCORD_WEBHOOK_URL', '')
DISCORD_ROLE_ID = _env('DISCORD_ROLE_ID', '')
DISCORD_COR = 0x0043ff
SITE_URL = _env('SITE_URL', '')

# Estoque de devoluções (MySQL, só leitura)
DB_HOST = _env('DB_HOST', '')
DB_PORT = _env('DB_PORT', 3306, int)
DB_USER = _env('DB_USER', '')
DB_PASS = _env('DB_PASS', '')
DB_NAME = _env('DB_NAME', '')
DB_SSL_CA = _env('DB_SSL_CA', '')
ESTOQUE_TABELA = 'estoque'
ESTOQUE_TIMEOUT = 5

# Worker
HEARTBEAT_FILE = _env('HEARTBEAT_FILE', '/tmp/ml-etiquetas-web.heartbeat')
