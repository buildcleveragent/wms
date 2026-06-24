from django.db import migrations


def backfill_payment_lines(apps, schema_editor):
    PosPayment = apps.get_model("pos", "PosPayment")
    PosPaymentLine = apps.get_model("pos", "PosPaymentLine")

    existing_sale_ids = set(
        PosPaymentLine.objects.values_list("sale_id", flat=True).distinct()
    )
    rows = []
    for payment in PosPayment.objects.exclude(sale_id__in=existing_sale_ids).iterator():
        rows.append(
            PosPaymentLine(
                sale_id=payment.sale_id,
                method=payment.method,
                amount=payment.amount_due,
                amount_received=payment.amount_received,
                change_amount=payment.change_amount,
                reference_no=payment.reference_no,
                status=payment.status,
            )
        )
    if rows:
        PosPaymentLine.objects.bulk_create(rows, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("pos", "0005_pos_return_payment_audit"),
    ]

    operations = [
        migrations.RunPython(backfill_payment_lines, migrations.RunPython.noop),
    ]
