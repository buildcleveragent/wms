from django.db import migrations


def backfill_bindings(apps, schema_editor):
    Customer = apps.get_model("baseinfo", "Customer")
    Binding = apps.get_model("baseinfo", "OwnerCustomerBinding")
    alias = schema_editor.connection.alias
    fields = (
        "code",
        "external_code",
        "salesperson_id",
        "area",
        "delivery_route",
        "delivery_seq",
        "level",
        "delivery_distance_km",
        "promised_days",
    )
    batch = []
    for customer in Customer._base_manager.using(alias).all().iterator(chunk_size=500):
        batch.append(
            Binding(
                customer_id=customer.pk,
                owner_id=customer.owner_id,
                **{field: getattr(customer, field) for field in fields},
            )
        )
        if len(batch) == 500:
            Binding.objects.using(alias).bulk_create(batch)
            batch = []
    if batch:
        Binding.objects.using(alias).bulk_create(batch)


class Migration(migrations.Migration):
    dependencies = [("baseinfo", "0006_ownercustomerbinding")]
    operations = [migrations.RunPython(backfill_bindings, migrations.RunPython.noop)]
