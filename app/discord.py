"""Envio de mensagens ao Discord por webhook."""
import logging

import requests

from app import config

log = logging.getLogger('discord')


def enviar(texto, titulo=None, descricao=None, pingar=False):
    if not config.DISCORD_WEBHOOK_URL:
        log.warning('DISCORD_WEBHOOK_URL vazio; mensagem não enviada: %s', texto)
        return False
    cargo = config.DISCORD_ROLE_ID if pingar else None
    corpo = {
        'username': 'ML Etiquetas',
        'content': (f'<@&{cargo}> | ' if cargo else '') + texto,
        'allowed_mentions': {'roles': [cargo] if cargo else []},
    }
    if titulo or descricao:
        corpo['embeds'] = [{'title': titulo, 'description': descricao, 'color': config.DISCORD_COR,
                            **({'url': config.SITE_URL} if config.SITE_URL else {})}]
    try:
        r = requests.post(config.DISCORD_WEBHOOK_URL, json=corpo, timeout=15)
        r.raise_for_status()
        return True
    except requests.RequestException as err:
        log.error('Falha ao enviar ao Discord: %s', err)
        return False
