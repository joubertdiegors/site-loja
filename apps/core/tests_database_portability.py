"""O projeto não pode depender do SQLite.

O banco oficial é o PostgreSQL; o SQLite existe só como saída de emergência
numa máquina de desenvolvimento sem Postgres instalado. Isso só continua sendo
verdade se ninguém escrever SQL específico de um dos dois.

O que estes testes provam **sem** precisar de um Postgres na máquina:

* nenhum SQL cru, nenhum ``RunSQL`` em migration, nenhum ``.extra()``;
* nenhum caminho de código que se comporte diferente por ``connection.vendor``;
* o padrão da configuração é PostgreSQL, e o SQLite é opt-in explícito.

O que eles **não** provam, e por isso está registrado em ``docs/OPERACAO.md``:
que a suíte inteira passa contra um PostgreSQL de verdade. Isso exige um
servidor Postgres e é feito no ambiente hospedado.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

#: SQL cru e escapes do ORM. Não são proibidos por princípio — são proibidos
#: sem uma decisão consciente, porque é por aí que a portabilidade se perde.
ESCAPES = re.compile(
    r"(\.raw\(|RawSQL|RunSQL|connection\.cursor\(|\.extra\()",
)

#: Ramificação por banco. Se aparecer, o código passa a ter dois
#: comportamentos e só um deles roda em produção.
POR_VENDOR = re.compile(r"connection\.vendor|connections\[[^\]]+\]\.vendor")

CODIGO = ("apps", "config")


def arquivos_python():
    raiz = Path(settings.BASE_DIR)
    for pasta in CODIGO:
        for caminho in (raiz / pasta).rglob("*.py"):
            texto = str(caminho.relative_to(raiz)).replace("\\", "/")
            if "/tests" in texto or "/test_" in texto or texto.endswith("_tests.py"):
                continue
            if "tests_" in Path(texto).name:
                continue
            yield texto, caminho


class NoDatabaseSpecificCodeTests(SimpleTestCase):
    def test_the_scan_reads_real_files(self):
        arquivos = list(arquivos_python())

        self.assertGreater(len(arquivos), 30)
        self.assertIn("config/settings.py", [nome for nome, _ in arquivos])

    def test_no_raw_sql_outside_the_orm(self):
        achados = []
        for nome, caminho in arquivos_python():
            for numero, linha in enumerate(
                caminho.read_text(encoding="utf-8").splitlines(), 1
            ):
                if ESCAPES.search(linha) and "get_extra" not in linha:
                    achados.append(f"{nome}:{numero}: {linha.strip()[:90]}")

        self.assertEqual(
            achados,
            [],
            "SQL fora do ORM — confira a portabilidade antes de manter:\n"
            + "\n".join(achados),
        )

    def test_no_branch_by_database_vendor(self):
        achados = [
            f"{nome}:{numero}"
            for nome, caminho in arquivos_python()
            for numero, linha in enumerate(
                caminho.read_text(encoding="utf-8").splitlines(), 1
            )
            if POR_VENDOR.search(linha)
        ]

        self.assertEqual(achados, [], f"Código que muda conforme o banco: {achados}")

    def test_no_migration_carries_manual_sql(self):
        raiz = Path(settings.BASE_DIR) / "apps"
        achados = [
            str(caminho.relative_to(settings.BASE_DIR))
            for caminho in raiz.rglob("migrations/*.py")
            if "RunSQL" in caminho.read_text(encoding="utf-8")
        ]

        self.assertEqual(achados, [], f"Migration com SQL manual: {achados}")


class DefaultsToPostgresTests(SimpleTestCase):
    def test_the_settings_default_is_postgres(self):
        """Sem `DJANGO_DB_ENGINE` no ambiente, o banco é o PostgreSQL."""
        fonte = (Path(settings.BASE_DIR) / "config" / "settings.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('env("DJANGO_DB_ENGINE", "postgres")', fonte)
        self.assertIn("django.db.backends.postgresql", fonte)

    def test_sqlite_is_opt_in(self):
        """O SQLite só entra quando alguém escreve `DJANGO_DB_ENGINE=sqlite`."""
        fonte = (Path(settings.BASE_DIR) / "config" / "settings.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('== "sqlite"', fonte)

    def test_the_requirements_carry_the_postgres_driver(self):
        base = (Path(settings.BASE_DIR) / "requirements" / "base.txt").read_text(
            encoding="utf-8"
        )

        self.assertIn("psycopg", base)


class ConcurrencyNoteTests(SimpleTestCase):
    """``select_for_update`` é o ponto onde os dois bancos divergem de verdade.

    No PostgreSQL ele trava a linha; no SQLite é aceito e **ignorado**. O código
    continua correto nos dois — o SQLite serializa a escrita no arquivo
    inteiro —, mas o teste de concorrência só vale no Postgres. Este teste
    existe para que o assunto não desapareça: se alguém acrescentar um lugar
    que dependa de trava de linha, a lista abaixo cresce e o revisor é obrigado
    a olhar.
    """

    #: Onde o projeto depende de trava de linha, hoje.
    LUGARES = {
        "apps/orders/models.py",  # numeração do pedido
        "apps/orders/services.py",  # baixa de estoque e confirmação de pagamento
        # A página de manutenção/lançamento: "só uma ativa". A trava serializa
        # dois administradores ativando ao mesmo tempo; no SQLite ela é
        # ignorada, mas o índice único parcial (`storefront_one_active_special_page`)
        # continua recusando a segunda ativa — a garantia não depende da trava.
        "apps/storefront/models.py",
    }

    def test_the_places_that_lock_rows_are_the_known_ones(self):
        raiz = Path(settings.BASE_DIR)
        encontrados = set()
        for nome, caminho in arquivos_python():
            texto = caminho.read_text(encoding="utf-8")
            if "select_for_update()" in texto:
                encontrados.add(nome)

        self.assertEqual(
            encontrados,
            self.LUGARES,
            "A lista de lugares que dependem de trava de linha mudou. "
            "Confirme que o novo caminho foi pensado para o PostgreSQL "
            "(no SQLite o select_for_update é aceito e ignorado) e atualize "
            "esta lista.",
        )
