"""Mensagem de vendas para o fornecedor (mesmo formato do py-vendas) e cruzamento
com o estoque de devoluções (MySQL, só leitura)."""
import re

from app import config

SAUDACAO = 'Bom dia! Tudo bem? Segue vendas de hoje:'

# Tratamentos de SKU, aplicados em ordem (regex, sem diferenciar maiúsculas).
TRATAMENTOS_SKU = {
    r'Kfi-': 'KF-',
    r'KIT-6-TP-SANGRADOR': 'Tp-016.K+016.P+016.T+016.MERV+016.UNIVER+023.GM',
}
RE_KIT = re.compile(r'^(\d+)\s*x\s*(.+)$', re.IGNORECASE)

# ---------------------------------------------------------------- número por extenso (feminino)

_UNIDADES = ['zero', 'uma', 'duas', 'três', 'quatro', 'cinco', 'seis', 'sete', 'oito', 'nove',
             'dez', 'onze', 'doze', 'treze', 'quatorze', 'quinze', 'dezesseis', 'dezessete',
             'dezoito', 'dezenove']
_DEZENAS = ['', '', 'vinte', 'trinta', 'quarenta', 'cinquenta', 'sessenta', 'setenta',
            'oitenta', 'noventa']
_CENTENAS = ['', 'cento', 'duzentas', 'trezentas', 'quatrocentas', 'quinhentas', 'seiscentas',
             'setecentas', 'oitocentas', 'novecentas']


def _ate_999(n):
    if n == 100:
        return 'cem'
    partes = []
    c, resto = divmod(n, 100)
    if c:
        partes.append(_CENTENAS[c])
    if resto >= 20:
        d, u = divmod(resto, 10)
        partes.append(_DEZENAS[d] + (f' e {_UNIDADES[u]}' if u else ''))
    elif resto:
        partes.append(_UNIDADES[resto])
    return ' e '.join(partes)


def por_extenso(n):
    """Inteiro (0 a 999.999) por extenso, no feminino: 22 -> 'vinte e duas'."""
    if n < 1000:
        return _ate_999(n)
    milhar, resto = divmod(n, 1000)
    texto = 'mil' if milhar == 1 else f'{_ate_999(milhar)} mil'
    if resto:
        sep = ' e ' if resto < 100 or resto % 100 == 0 else ' '
        texto += sep + _ate_999(resto)
    return texto

# ---------------------------------------------------------------- mensagem


def tratar_item(sku, qtd):
    """Aplica os tratamentos de SKU e o multiplicador de kit ("2x KF-101")."""
    for padrao, troca in TRATAMENTOS_SKU.items():
        sku = re.sub(padrao, troca, sku, flags=re.IGNORECASE)
    m = RE_KIT.match(sku)
    if m:
        qtd *= int(m.group(1))
        sku = m.group(2).strip()
    return sku, qtd


def formatar_item(sku, qtd):
    if qtd > 1:
        return f'*{qtd}x* {sku} *({por_extenso(qtd)} unidades)*'
    return f'{qtd}x {sku}'


def gerar(vendas):
    """vendas: [{'comprador': str, 'itens': [{'sku', 'titulo', 'qtd'}]}].

    Devolve (mensagem, avisos, vendidos), vendidos = [(nome, sku_tratado, qtd)].
    """
    linhas, avisos, vendidos = [], [], []
    for v in vendas:
        nome = v.get('comprador') or '(sem nome)'
        itens = []
        for it in v.get('itens') or []:
            sku = it.get('sku') or ''
            if not sku:
                avisos.append(f'{nome}: anúncio sem SKU ({it.get("titulo")}).')
                sku = it.get('titulo') or '?'
            sku, qtd = tratar_item(sku, it.get('qtd') or 1)
            itens.append(formatar_item(sku, qtd))
            vendidos.append((nome, sku, qtd))
        linhas.append(f'- {nome}: ' + ' + '.join(itens))
    return '\n'.join([SAUDACAO, *linhas]), avisos, vendidos

# ---------------------------------------------------------------- estoque de devoluções


def buscar_estoque():
    """{SKU_MAIUSCULO: (sku_original, qtd)} dos SKUs com qtd > 0."""
    import pymysql

    tls = {}
    if config.DB_SSL_CA:
        # A CA confere o certificado; o certificado auto-gerado do MySQL não tem o hostname.
        tls = dict(ssl_ca=config.DB_SSL_CA, ssl_verify_cert=True, ssl_verify_identity=False)
    conexao = pymysql.connect(
        **tls, host=config.DB_HOST, port=config.DB_PORT, user=config.DB_USER,
        password=config.DB_PASS, database=config.DB_NAME, charset='utf8mb4',
        connect_timeout=config.ESTOQUE_TIMEOUT, read_timeout=config.ESTOQUE_TIMEOUT,
    )
    try:
        with conexao.cursor() as cur:
            cur.execute('SET SESSION TRANSACTION READ ONLY')
            cur.execute(f'SELECT sku, qtd FROM `{config.ESTOQUE_TABELA}` WHERE qtd > 0')
            return {sku.strip().upper(): (sku.strip(), qtd) for sku, qtd in cur.fetchall()}
    finally:
        conexao.close()


def partes_do_sku(sku):
    """Separa kits por '+', herdando o prefixo da primeira parte (Tp-016.K+016.P -> TP-016.K, TP-016.P),
    expande macho+fêmea (TP-036.M+F.GM2) e corrige 'TP.036' -> 'TP-036'."""
    sku = re.sub(r'\b([A-Z]+)\.(\d)', r'\1-\2', sku.upper())
    sku = re.sub(r'([A-Z]+-\d+)\.M\+F\.([A-Z0-9]+)', r'\1.M.\2+\1.F.\2', sku)
    partes = [p.strip() for p in sku.split('+') if p.strip()]
    if not partes:
        return []
    m = re.match(r'^([A-Z]+-)', partes[0])
    prefixo = m.group(1) if m else ''
    return [prefixo + p if prefixo and re.match(r'^\d', p) else p for p in partes]


def base_do_sku(sku):
    return sku.split('.', 1)[0]


def cruzar_estoque(vendidos, estoque):
    """(exatos, parecidos): listas de dicts nome, vendido, levando, no_estoque, total."""
    por_base = {}
    for chave in estoque:
        por_base.setdefault(base_do_sku(chave), []).append(chave)

    exatos, parecidos = [], []
    for nome, sku, qtd in vendidos:
        for parte in partes_do_sku(sku):
            if parte in estoque:
                exatos.append((nome, parte, qtd, *estoque[parte]))
                continue
            for chave in sorted(por_base.get(base_do_sku(parte), [])):
                parecidos.append((nome, parte, qtd, *estoque[chave]))
    ja = {(n, s.upper()) for n, _, _, s, _ in exatos}
    parecidos = [p for p in parecidos if (p[0], p[3].upper()) not in ja]

    def d(t):
        return dict(zip(('nome', 'vendido', 'levando', 'no_estoque', 'total'), t))
    return [d(t) for t in exatos], [d(t) for t in parecidos]


def estoque_para(vendidos):
    """Resultado do cruzamento para mostrar no site; nunca levanta exceção."""
    if not config.DB_HOST:
        return {'erro': 'estoque não configurado', 'exatos': [], 'parecidos': []}
    try:
        exatos, parecidos = cruzar_estoque(vendidos, buscar_estoque())
        return {'erro': None, 'exatos': exatos, 'parecidos': parecidos}
    except Exception as err:  # sem rede, senha errada...
        return {'erro': f'{type(err).__name__}: {err}', 'exatos': [], 'parecidos': []}
