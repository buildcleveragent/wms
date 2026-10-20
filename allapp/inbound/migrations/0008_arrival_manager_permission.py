from django.db import migrations


def grant(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    alias = schema_editor.connection.alias
    ct, _ = ContentType.objects.using(alias).get_or_create(
        app_label="inbound", model="inboundorder"
    )
    permission, _ = Permission.objects.using(alias).get_or_create(
        content_type=ct, codename="manage_arrival", defaults={"name": "PDA 管理入库申请和分批验收"}
    )
    group = Group.objects.using(alias).filter(name="WMS::仓库管理员").first()
    if group:
        group.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [("inbound", "0007_arrivalcommand_arrivalevent_arrivalphoto_and_more")]
    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
