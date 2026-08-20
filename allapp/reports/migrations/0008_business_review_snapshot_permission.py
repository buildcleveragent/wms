from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("reports", "0007_factoutboundordersla")]

    operations = [
        migrations.AlterModelOptions(
            name="businessreviewsnapshot",
            options={
                "permissions": [
                    (
                        "create_business_review_snapshot",
                        "可创建并分享不可变经营例会快照",
                    )
                ]
            },
        )
    ]
