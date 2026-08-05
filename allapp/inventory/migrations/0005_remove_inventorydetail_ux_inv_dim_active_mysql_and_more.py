import datetime
import django.db.models.deletion
import django.db.models.functions.comparison
import django.db.models.functions.text
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('baseinfo', '0005_ownerwarehousebinding'),
        ('inbound', '0004_operations_report_indexes'),
        ('inventory', '0004_alter_inventory_zone_type'),
        ('locations', '0004_alter_location_zone_type'),
        ('products', '0004_productcategory_mall_fields'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='inventorydetail',
            name='ux_inv_dim_active_mysql',
        ),
        migrations.AddField(
            model_name='inventorydetail',
            name='container',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='inventory_details', to='locations.container', verbose_name='容器'),
        ),
        migrations.AddField(
            model_name='inventorytransaction',
            name='container',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='inventory_transactions', to='locations.container', verbose_name='容器'),
        ),
        migrations.AddField(
            model_name='inventorytransaction',
            name='container_no',
            field=models.CharField(blank=True, default='', max_length=60, verbose_name='容器号快照'),
        ),
        migrations.AddIndex(
            model_name='inventorydetail',
            index=models.Index(fields=['warehouse', 'location', 'container', 'product'], name='idx_inv_wh_loc_cont_prod'),
        ),
        migrations.AddIndex(
            model_name='inventorytransaction',
            index=models.Index(fields=['warehouse', 'container'], name='idx_tx_wh_container'),
        ),
        migrations.AddConstraint(
            model_name='inventorydetail',
            constraint=models.UniqueConstraint(models.F('owner'), models.F('product'), models.F('warehouse'), models.F('location'), django.db.models.functions.comparison.Coalesce(models.F('container'), models.Value(0), output_field=models.BigIntegerField()), django.db.models.functions.text.Upper(django.db.models.functions.comparison.Coalesce(models.F('batch_no'), models.Value(''))), django.db.models.functions.comparison.Coalesce(models.F('production_date'), models.Value(datetime.date(1000, 1, 1))), django.db.models.functions.comparison.Coalesce(models.F('expiry_date'), models.Value(datetime.date(1000, 1, 1))), django.db.models.functions.text.Upper(django.db.models.functions.comparison.Coalesce(models.F('serial_no'), models.Value(''))), models.F('is_active'), name='ux_inv_dim_active_mysql'),
        ),
    ]
