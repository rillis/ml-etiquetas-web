"""Arquivos JSON de estado em data/estado, com escrita atômica e trava entre processos."""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from app import config

TZ = ZoneInfo(config.TIMEZONE)


def agora():
    return datetime.now(TZ)


def carimbo(momento=None):
    return (momento or agora()).strftime('%Y%m%d-%H%M')


def _caminho(nome):
    config.ESTADO_DIR.mkdir(parents=True, exist_ok=True)
    return config.ESTADO_DIR / f'{nome}.json'


def ler_json(caminho, padrao=None):
    try:
        with open(caminho, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return padrao


def ler(nome, padrao=None):
    return ler_json(_caminho(nome), padrao)


def gravar_json(caminho, dados):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=caminho.parent, prefix='.tmp-', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(dados, f, ensure_ascii=False, indent=1)
        os.replace(tmp, caminho)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def gravar(nome, dados):
    gravar_json(_caminho(nome), dados)


def apagar(nome):
    try:
        _caminho(nome).unlink()
    except FileNotFoundError:
        pass


@contextmanager
def trava(nome='geral'):
    config.ESTADO_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.ESTADO_DIR / f'.{nome}.lock', 'a') as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
