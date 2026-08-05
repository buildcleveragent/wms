import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('baseinfo', '0005_ownerwarehousebinding'),
        ('inventory', '0005_remove_inventorydetail_ux_inv_dim_active_mysql_and_more'),
        ('locations', '0004_alter_location_zone_type'),
        ('tasking', '0007_replenishment_feature'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='reloctaskextra',
            name='ck_rlc_zones_diff',
        ),
        migrations.AlterField(
            model_name='reloclineextra',
            name='from_lpn',
            field=models.CharField(blank=True, db_index=True, default='', max_length=60, verbose_name='上游容器号'),
        ),
        migrations.AlterField(
            model_name='reloclineextra',
            name='to_lpn',
            field=models.CharField(blank=True, db_index=True, default='', max_length=60, verbose_name='目标容器号'),
        ),
        migrations.AlterField(
            model_name='taskscanlog',
            name='container_no',
            field=models.CharField(blank=True, max_length=60, null=True, verbose_name='容器/托盘号'),
        ),
        migrations.AddField(
            model_name='reloclineextra',
            name='from_container',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='relocation_source_lines', to='locations.container'),
        ),
        migrations.AddField(
            model_name='reloclineextra',
            name='to_container',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='relocation_target_lines', to='locations.container'),
        ),
        migrations.AddField(
            model_name='reloctaskextra',
            name='exception_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='relocation_exceptions_handled', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='reloctaskextra',
            name='exception_code',
            field=models.CharField(blank=True, default='', max_length=30, verbose_name='异常代码'),
        ),
        migrations.AddField(
            model_name='reloctaskextra',
            name='exception_note',
            field=models.CharField(blank=True, default='', max_length=200, verbose_name='异常说明'),
        ),
        migrations.AddField(
            model_name='reloctaskextra',
            name='execution_state',
            field=models.CharField(choices=[('READY', '待执行'), ('WORKING', '执行中'), ('EXCEPTION', '异常暂停'), ('POSTING_FAILED', '过账失败'), ('DONE', '已完成')], db_index=True, default='READY', max_length=20, verbose_name='执行状态'),
        ),
        migrations.AddField(
            model_name='reloctaskextra',
            name='reason',
            field=models.CharField(blank=True, default='', max_length=200, verbose_name='移库原因'),
        ),
        migrations.AddField(
            model_name='reloctaskextra',
            name='root_container',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='relocation_tasks_as_root', to='locations.container'),
        ),
        migrations.AddField(
            model_name='reloctaskextra',
            name='target_parent_container',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='relocation_tasks_as_target_parent', to='locations.container'),
        ),
        migrations.AddField(
            model_name='reloctaskextra',
            name='trigger',
            field=models.CharField(choices=[('REQUEST', '操作员申请'), ('DIRECT', '经理直接下发')], default='REQUEST', max_length=10, verbose_name='触发类型'),
        ),
        migrations.CreateModel(
            name='RelocationRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_deleted', models.BooleanField(default=False, verbose_name='已删除')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='删除时间')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('is_active', models.BooleanField(default=True, verbose_name='启用状态')),
                ('remark', models.CharField(blank=True, max_length=200, null=True, verbose_name='备注')),
                ('mode', models.CharField(choices=[('LAYER', '库存层'), ('CONTAINER', '整容器')], max_length=12, verbose_name='申请模式')),
                ('trigger', models.CharField(choices=[('REQUEST', '操作员申请'), ('DIRECT', '经理直接下发')], default='REQUEST', max_length=10, verbose_name='触发类型')),
                ('reason', models.CharField(max_length=200, verbose_name='申请原因')),
                ('status', models.CharField(choices=[('PENDING', '待审核'), ('APPROVED', '已通过'), ('REJECTED', '已驳回'), ('CANCELLED', '已取消')], db_index=True, default='PENDING', max_length=12, verbose_name='状态')),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('review_note', models.CharField(blank=True, default='', max_length=200, verbose_name='审核意见')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='%(class)s_created', to=settings.AUTH_USER_MODEL, verbose_name='创建人')),
                ('deleted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='%(class)s_deleted', to=settings.AUTH_USER_MODEL, verbose_name='删除人')),
                ('generated_task', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='relocation_requests', to='tasking.wmstask')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='relocation_requests', to='baseinfo.owner')),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='relocation_requests_reviewed', to=settings.AUTH_USER_MODEL)),
                ('source_container', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='relocation_requests_as_source', to='locations.container')),
                ('target_parent_container', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='relocation_requests_as_target_parent', to='locations.container')),
                ('to_location', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='container_relocation_requests', to='locations.location')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='%(class)s_updated', to=settings.AUTH_USER_MODEL, verbose_name='更新人')),
                ('warehouse', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='relocation_requests', to='locations.warehouse')),
            ],
            options={
                'verbose_name': '移库申请',
                'verbose_name_plural': '移库申请',
                'permissions': [('request_relocation', '申请移库'), ('approve_relocation', '审核移库申请'), ('manage_relocation', '管理移库任务')],
            },
        ),
        migrations.AddField(
            model_name='reloctaskextra',
            name='request',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='task_extra', to='tasking.relocationrequest'),
        ),
        migrations.CreateModel(
            name='RelocationRequestLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_deleted', models.BooleanField(default=False, verbose_name='已删除')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='删除时间')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('is_active', models.BooleanField(default=True, verbose_name='启用状态')),
                ('remark', models.CharField(blank=True, max_length=200, null=True, verbose_name='备注')),
                ('requested_qty', models.DecimalField(decimal_places=4, max_digits=18, verbose_name='申请数量')),
                ('source_snapshot', models.JSONField(blank=True, default=dict, verbose_name='来源库存快照')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='%(class)s_created', to=settings.AUTH_USER_MODEL, verbose_name='创建人')),
                ('deleted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='%(class)s_deleted', to=settings.AUTH_USER_MODEL, verbose_name='删除人')),
                ('inventory_detail', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='relocation_request_lines', to='inventory.inventorydetail')),
                ('request', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='lines', to='tasking.relocationrequest')),
                ('to_container', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='relocation_request_lines_as_target', to='locations.container')),
                ('to_location', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='relocation_request_lines', to='locations.location')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='%(class)s_updated', to=settings.AUTH_USER_MODEL, verbose_name='更新人')),
            ],
            options={
                'verbose_name': '移库申请行',
                'verbose_name_plural': '移库申请行',
            },
        ),
        migrations.CreateModel(
            name='RelocationReservation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_deleted', models.BooleanField(default=False, verbose_name='已删除')),
                ('deleted_at', models.DateTimeField(blank=True, null=True, verbose_name='删除时间')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('is_active', models.BooleanField(default=True, verbose_name='启用状态')),
                ('remark', models.CharField(blank=True, max_length=200, null=True, verbose_name='备注')),
                ('qty', models.DecimalField(decimal_places=4, max_digits=18, verbose_name='预留数量')),
                ('status', models.CharField(choices=[('ACTIVE', '活动'), ('CONSUMED', '已消费'), ('RELEASED', '已释放')], db_index=True, default='ACTIVE', max_length=10, verbose_name='状态')),
                ('consumed_at', models.DateTimeField(blank=True, null=True)),
                ('released_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='%(class)s_created', to=settings.AUTH_USER_MODEL, verbose_name='创建人')),
                ('deleted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='%(class)s_deleted', to=settings.AUTH_USER_MODEL, verbose_name='删除人')),
                ('inventory_detail', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='relocation_reservations', to='inventory.inventorydetail')),
                ('task_line', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='relocation_reservation', to='tasking.wmstaskline')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='%(class)s_updated', to=settings.AUTH_USER_MODEL, verbose_name='更新人')),
            ],
            options={
                'verbose_name': '移库预留',
                'verbose_name_plural': '移库预留',
            },
        ),
        migrations.AddIndex(
            model_name='relocationrequest',
            index=models.Index(fields=['warehouse', 'status', 'created_at'], name='ix_reloc_req_queue'),
        ),
        migrations.AddIndex(
            model_name='relocationrequest',
            index=models.Index(fields=['created_by', 'status', 'created_at'], name='ix_reloc_req_creator'),
        ),
        migrations.AddIndex(
            model_name='relocationrequestline',
            index=models.Index(fields=['request', 'inventory_detail'], name='ix_reloc_req_line_src'),
        ),
        migrations.AddConstraint(
            model_name='relocationrequestline',
            constraint=models.CheckConstraint(condition=models.Q(('requested_qty__gt', 0)), name='ck_reloc_req_line_qty'),
        ),
        migrations.AddIndex(
            model_name='relocationreservation',
            index=models.Index(fields=['inventory_detail', 'status'], name='ix_reloc_res_src_status'),
        ),
        migrations.AddConstraint(
            model_name='relocationreservation',
            constraint=models.CheckConstraint(condition=models.Q(('qty__gt', 0)), name='ck_reloc_res_qty'),
        ),
    ]
