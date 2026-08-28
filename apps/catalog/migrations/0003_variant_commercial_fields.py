"""Etapa 8 (1/3) — a variante ganha os campos comerciais.

Só acrescenta. Nada é removido do produto aqui: a migration seguinte
(``0004_move_commercial_data_to_variants``) copia os dados, e só depois a
``0005`` limpa o produto.
"""

import datetime
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0002_product_personalization_text_limit_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='productvariant',
            name='allow_backorder',
            field=models.BooleanField(default=False, help_text='Permite vender mesmo com estoque zerado.', verbose_name='permitir venda sem estoque'),
        ),
        migrations.AddField(
            model_name='productvariant',
            name='dimension_unit',
            field=models.CharField(choices=[('mm', 'Milímetro (mm)'), ('cm', 'Centímetro (cm)'), ('m', 'Metro (m)'), ('in', 'Polegada (in)')], default='mm', max_length=4, verbose_name='unidade das dimensões'),
        ),
        migrations.AddField(
            model_name='productvariant',
            name='energy_cost',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10, verbose_name='custo de energia'),
        ),
        migrations.AddField(
            model_name='productvariant',
            name='filament_cost',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10, verbose_name='custo de filamento'),
        ),
        migrations.AddField(
            model_name='productvariant',
            name='made_to_order',
            field=models.BooleanField(default=False, verbose_name='produzida sob encomenda'),
        ),
        migrations.AddField(
            model_name='productvariant',
            name='pricing_mode',
            field=models.CharField(choices=[('price', 'Informar preço de venda (margem calculada)'), ('margin', 'Informar margem de lucro (preço calculado)')], default='price', max_length=8, verbose_name='definir preço por'),
        ),
        migrations.AddField(
            model_name='productvariant',
            name='print_time',
            field=models.DurationField(blank=True, help_text='Formato HH:MM:SS. Ex.: 02:35:00 para 2 horas e 35 minutos.', null=True, verbose_name='tempo de impressão'),
        ),
        migrations.AddField(
            model_name='productvariant',
            name='production_lead_time_days',
            field=models.PositiveIntegerField(blank=True, help_text='Obrigatório sob encomenda. Entra no prazo mostrado ao cliente.', null=True, verbose_name='prazo de produção (dias)'),
        ),
        migrations.AddField(
            model_name='productvariant',
            name='profit_margin',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Margem sobre o preço de venda: (preço - custo) / preço × 100.', max_digits=5, null=True, verbose_name='margem de lucro (%)'),
        ),
        migrations.AddField(
            model_name='productvariant',
            name='total_cost',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), editable=False, help_text='Calculado automaticamente a partir dos componentes de custo.', max_digits=10, verbose_name='custo total'),
        ),
        migrations.AlterField(
            model_name='productvariant',
            name='sale_price',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='O preço desta variante. É ele que o cliente paga.', max_digits=10, null=True, verbose_name='preço de venda'),
        ),
        migrations.AlterField(
            model_name='productvariant',
            name='weight_grams',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Sempre em gramas. É este peso que calcula o frete.', max_digits=10, null=True, verbose_name='peso (g)'),
        ),
        migrations.AddConstraint(
            model_name='productvariant',
            constraint=models.CheckConstraint(condition=models.Q(('filament_cost__gte', 0)), name='variant_filament_cost_not_negative'),
        ),
        migrations.AddConstraint(
            model_name='productvariant',
            constraint=models.CheckConstraint(condition=models.Q(('energy_cost__gte', 0)), name='variant_energy_cost_not_negative'),
        ),
        migrations.AddConstraint(
            model_name='productvariant',
            constraint=models.CheckConstraint(condition=models.Q(('total_cost__gte', 0)), name='variant_total_cost_not_negative'),
        ),
        migrations.AddConstraint(
            model_name='productvariant',
            constraint=models.CheckConstraint(condition=models.Q(('profit_margin__isnull', True), models.Q(('profit_margin__gte', Decimal('-999.99')), ('profit_margin__lte', Decimal('100.00'))), _connector='OR'), name='variant_profit_margin_within_bounds'),
        ),
        migrations.AddConstraint(
            model_name='productvariant',
            constraint=models.CheckConstraint(condition=models.Q(('width__isnull', True), ('width__gte', 0), _connector='OR'), name='variant_width_not_negative'),
        ),
        migrations.AddConstraint(
            model_name='productvariant',
            constraint=models.CheckConstraint(condition=models.Q(('height__isnull', True), ('height__gte', 0), _connector='OR'), name='variant_height_not_negative'),
        ),
        migrations.AddConstraint(
            model_name='productvariant',
            constraint=models.CheckConstraint(condition=models.Q(('depth__isnull', True), ('depth__gte', 0), _connector='OR'), name='variant_depth_not_negative'),
        ),
        migrations.AddConstraint(
            model_name='productvariant',
            constraint=models.CheckConstraint(condition=models.Q(('print_time__isnull', True), ('print_time__gte', datetime.timedelta(0)), _connector='OR'), name='variant_print_time_not_negative'),
        ),
    ]
