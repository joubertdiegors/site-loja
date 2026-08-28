"""Guarda de segredos que precisam voltar em texto puro.

A senha do SMTP não é uma senha de login: ela não pode ser transformada num
hash, porque o servidor de e-mail precisa dela inteira na hora de autenticar.
Guardá-la em texto puro no banco significaria que **todo backup do banco vira
um vazamento de credencial** — e o procedimento de backup deste projeto
(``docs/OPERACAO.md``) manda copiar o banco para fora do servidor.

Então ela é cifrada antes de entrar no banco, com uma chave derivada do
``DJANGO_SECRET_KEY``, que vive no ``.env`` e **não** no banco. Quem tiver só o
dump não tem a chave; quem tiver só o ``.env`` não tem o dado.

O que isto NÃO protege: alguém com acesso ao servidor inteiro (banco + ``.env``
+ código) lê a senha, como leria o ``.env`` direto. Não existe forma de guardar
uma credencial reutilizável que resista a isso — o que existe é reduzir a
superfície, e é o que este módulo faz.

Trocar o ``DJANGO_SECRET_KEY`` invalida o que foi cifrado com o anterior: a
senha volta como vazia e precisa ser digitada de novo no Admin. É o
comportamento certo — melhor um campo vazio e visível do que um valor
silenciosamente errado.
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)

#: Rótulo do uso. Duas finalidades diferentes derivam chaves diferentes a
#: partir da mesma SECRET_KEY, para um valor cifrado num contexto não poder ser
#: decifrado noutro.
DEFAULT_PURPOSE = "jdprint.secrets.v1"


def _fernet(purpose: str = DEFAULT_PURPOSE) -> Fernet:
    material = f"{purpose}:{settings.SECRET_KEY}".encode()
    chave = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
    return Fernet(chave)


def encrypt(value: str, purpose: str = DEFAULT_PURPOSE) -> str:
    """Cifra um segredo para gravação. String vazia continua vazia."""
    if not value:
        return ""
    return _fernet(purpose).encrypt(value.encode()).decode()


def decrypt(token: str, purpose: str = DEFAULT_PURPOSE) -> str:
    """Devolve o segredo, ou string vazia se ele não puder ser lido.

    Não levanta: um token ilegível (SECRET_KEY trocada, coluna corrompida,
    valor migrado de outro ambiente) tem que virar "sem senha configurada" e
    não uma página de erro no meio de um envio de pedido. O log registra que
    houve um token e que ele não abriu — sem o token e sem o valor.
    """
    if not token:
        return ""
    try:
        return _fernet(purpose).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        logger.warning(
            "Segredo gravado não pôde ser decifrado (%s). "
            "A DJANGO_SECRET_KEY mudou? Regrave o valor no Admin.",
            purpose,
        )
        return ""


def mask(value: str) -> str:
    """Como um segredo aparece numa tela ou num log: nunca inteiro.

    Segredo curto vira só pontinhos — mostrar dois caracteres de uma senha de
    quatro é entregar metade dela.
    """
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * 8
    return f"{value[:2]}{'•' * 6}{value[-2:]}"
