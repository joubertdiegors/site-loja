"""Etapa 8 (3/3) — o produto fica só com o que é genérico.

Roda **depois** de ``0004``, que já copiou preço, custo, estoque, peso,
dimensões e prazo para as variantes. Nada é perdido: os dados foram para a
tabela onde passam a viver.
"""

import datetime
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0004_move_commercial_data_to_variants'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='product',
            name='product_filament_cost_not_negative',
        ),
        migrations.RemoveConstraint(
            model_name='product',
            name='product_energy_cost_not_negative',
        ),
        migrations.RemoveConstraint(
            model_name='product',
            name='product_total_cost_not_negative',
        ),
        migrations.RemoveConstraint(
            model_name='product',
            name='product_sale_price_not_negative',
        ),
        migrations.RemoveConstraint(
            model_name='product',
            name='product_profit_margin_within_bounds',
        ),
        migrations.RemoveConstraint(
            model_name='product',
            name='product_weight_not_negative',
        ),
        migrations.RemoveConstraint(
            model_name='product',
            name='product_width_not_negative',
        ),
        migrations.RemoveConstraint(
            model_name='product',
            name='product_height_not_negative',
        ),
        migrations.RemoveConstraint(
            model_name='product',
            name='product_depth_not_negative',
        ),
        migrations.RemoveConstraint(
            model_name='product',
            name='product_print_time_not_negative',
        ),
        migrations.RemoveField(
            model_name='product',
            name='allow_backorder',
        ),
        migrations.RemoveField(
            model_name='product',
            name='colors',
        ),
        migrations.RemoveField(
            model_name='product',
            name='depth',
        ),
        migrations.RemoveField(
            model_name='product',
            name='dimension_unit',
        ),
        migrations.RemoveField(
            model_name='product',
            name='energy_cost',
        ),
        migrations.RemoveField(
            model_name='product',
            name='filament_cost',
        ),
        migrations.RemoveField(
            model_name='product',
            name='height',
        ),
        migrations.RemoveField(
            model_name='product',
            name='made_to_order',
        ),
        migrations.RemoveField(
            model_name='product',
            name='materials',
        ),
        migrations.RemoveField(
            model_name='product',
            name='pricing_mode',
        ),
        migrations.RemoveField(
            model_name='product',
            name='print_time',
        ),
        migrations.RemoveField(
            model_name='product',
            name='production_lead_time_days',
        ),
        migrations.RemoveField(
            model_name='product',
            name='profit_margin',
        ),
        migrations.RemoveField(
            model_name='product',
            name='sale_price',
        ),
        migrations.RemoveField(
            model_name='product',
            name='stock_quantity',
        ),
        migrations.RemoveField(
            model_name='product',
            name='total_cost',
        ),
        migrations.RemoveField(
            model_name='product',
            name='weight_grams',
        ),
        migrations.RemoveField(
            model_name='product',
            name='width',
        ),
    ]
