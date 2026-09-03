"""A conta bancária deixa de ser uma linha só.

Escrita à mão, e não gerada, por causa de uma palavra: **renomear**.

O autodetector do Django não sabe que ``BankAccount`` é ``BankTransferSettings``
com outro nome — ele vê um model que sumiu e um que apareceu, e escreve
``DeleteModel`` + ``CreateModel``. Isso apagaria a conta cadastrada: o IBAN da
loja, digitado por alguém, sumiria na hora do ``migrate`` em produção sem
nenhum aviso.

``RenameModel`` renomeia a tabela e leva as linhas junto. Depois dela, o
autodetector volta a enxergar o mesmo model e gera o resto normalmente
(0007) — os campos novos, o `PaymentProof` e a marca de envio no pedido.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0005_banktransfersettings"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="BankTransferSettings",
            new_name="BankAccount",
        ),
    ]
