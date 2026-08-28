"""Nenhum segredo entra no que o Git carrega.

A auditoria da etapa 10 varreu o repositório e não achou nada. Este teste é o
que impede que ela precise ser refeita à mão: ele roda a mesma varredura em
toda execução da suíte.

Duas coisas são procuradas, e elas são diferentes:

1. **chave em formato real** — ``sk_live_``, ``whsec_``, bloco PEM de chave
   privada, credencial da AWS. Qualquer uma delas é falha imediata, em qualquer
   arquivo, inclusive teste: uma chave de teste da Stripe também é uma chave;
2. **arquivo que nunca pode ser versionado** — ``.env``, banco, log, uploads.

O que o teste **não** procura é a palavra "password": ela aparece 294 vezes no
projeto, quase toda vez como nome de campo, docstring ou fixture de teste.
Alarme que dispara sempre acaba ignorado.
"""

import re
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

#: Formatos que só existem quando alguém colou uma credencial de verdade.
CHAVES_REAIS = re.compile(
    r"(sk_live_[A-Za-z0-9]{10,}"
    r"|sk_test_[A-Za-z0-9]{20,}"
    r"|rk_live_[A-Za-z0-9]{10,}"
    r"|whsec_[A-Za-z0-9]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|AKIA[0-9A-Z]{16}"
    r"|ghp_[A-Za-z0-9]{36})"
)

#: Caminhos que nunca podem estar sob controle de versão.
PROIBIDOS = (
    re.compile(r"^\.env$"),
    re.compile(r"^\.env\.(?!example$)"),
    re.compile(r"\.sqlite3?$"),
    re.compile(r"^db\.sqlite3"),
    re.compile(r"\.log$"),
    re.compile(r"^logs/"),
    re.compile(r"^staticfiles/"),
    re.compile(r"^media/(?!\.gitkeep$)"),
    re.compile(r"\.(pem|key|p12|pfx)$"),
    re.compile(r"^venv/"),
    re.compile(r"^node_modules/"),
)

BINARIOS = (".mo", ".woff2", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".pdf")


def arquivos_versionados() -> list[str]:
    resultado = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        cwd=str(settings.BASE_DIR),
    )
    if resultado.returncode != 0:
        return []
    return [linha for linha in resultado.stdout.splitlines() if linha.strip()]


class SecretsAuditTests(SimpleTestCase):
    def setUp(self):
        self.arquivos = arquivos_versionados()
        if not self.arquivos:
            self.skipTest("fora de um repositório git")

    def test_the_audit_actually_reads_the_repository(self):
        """Uma varredura vazia passaria em tudo sem olhar nada."""
        self.assertGreater(len(self.arquivos), 50)
        self.assertIn("config/settings.py", self.arquivos)

    def test_no_real_key_is_versioned(self):
        achados = []
        raiz = Path(settings.BASE_DIR)
        for caminho in self.arquivos:
            if caminho.endswith(BINARIOS):
                continue
            arquivo = raiz / caminho
            if not arquivo.exists():
                continue
            try:
                texto = arquivo.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for numero, linha in enumerate(texto.splitlines(), 1):
                achado = CHAVES_REAIS.search(linha)
                if achado:
                    achados.append(f"{caminho}:{numero} ({achado.group(0)[:12]}…)")

        self.assertEqual(
            achados,
            [],
            "Credencial em formato real dentro do repositório:\n" + "\n".join(achados),
        )

    def test_no_forbidden_file_is_versioned(self):
        achados = [
            caminho
            for caminho in self.arquivos
            if any(padrao.search(caminho) for padrao in PROIBIDOS)
        ]

        self.assertEqual(
            achados,
            [],
            "Arquivo que não pode ser versionado:\n" + "\n".join(achados),
        )

    def test_the_env_example_has_no_values_for_the_secrets(self):
        """O exemplo documenta os nomes. Valor de segredo, nunca."""
        exemplo = Path(settings.BASE_DIR) / ".env.example"
        self.assertTrue(exemplo.exists(), "falta o .env.example")

        segredos = (
            "STRIPE_SECRET_KEY",
            "STRIPE_WEBHOOK_SECRET",
            "EMAIL_HOST_PASSWORD",
        )
        preenchidos = []
        for linha in exemplo.read_text(encoding="utf-8").splitlines():
            for nome in segredos:
                if linha.startswith(f"{nome}=") and linha.split("=", 1)[1].strip():
                    preenchidos.append(linha)

        self.assertEqual(
            preenchidos,
            [],
            "O .env.example tem valor onde deveria ter só o nome:\n"
            + "\n".join(preenchidos),
        )

    def test_the_env_example_documents_every_secret_the_project_reads(self):
        """Variável que o código lê e o exemplo não cita vira surpresa no deploy."""
        exemplo = (Path(settings.BASE_DIR) / ".env.example").read_text(encoding="utf-8")
        esperados = (
            "DJANGO_SECRET_KEY",
            "DJANGO_ALLOWED_HOSTS",
            "DJANGO_CSRF_TRUSTED_ORIGINS",
            "POSTGRES_PASSWORD",
            "STRIPE_SECRET_KEY",
            "STRIPE_WEBHOOK_SECRET",
            "EMAIL_HOST",
            "EMAIL_HOST_USER",
            "EMAIL_HOST_PASSWORD",
        )
        faltando = [nome for nome in esperados if f"{nome}=" not in exemplo]

        self.assertEqual(faltando, [], f"Sem entrada no .env.example: {faltando}")

    def test_the_gitignore_covers_the_dangerous_paths(self):
        ignore = (Path(settings.BASE_DIR) / ".gitignore").read_text(encoding="utf-8")
        for padrao in (".env", "media/", "*.log", "staticfiles/", "__pycache__/", "venv/"):
            with self.subTest(padrao=padrao):
                self.assertIn(padrao, ignore)

    def test_the_env_example_itself_stays_versioned(self):
        """A exceção do .gitignore precisa continuar valendo."""
        self.assertIn(".env.example", self.arquivos)
