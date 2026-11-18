"""List every protected reseller master and repair only an explicitly reviewed target."""

import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from allapp.products.models import TransferProductBinding
from allapp.products.transfer_audit import repair_binding, report_binding


class Command(BaseCommand):
    help = "只读检查所有转货商品；--apply 需指定绑定、审核签名和操作人，且重新检查所有条件。"

    def add_arguments(self, parser):
        parser.add_argument("--binding-id", type=int)
        parser.add_argument("--target-product", type=int)
        parser.add_argument("--price-basis", choices=("current", "source"))
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--expected-signature")
        parser.add_argument("--actor-id", type=int)

    def handle(self, *args, **options):
        if options["apply"]:
            if not all(options[key] for key in ("binding_id", "expected_signature", "actor_id")):
                raise CommandError("执行需指定 --binding-id、--expected-signature、--actor-id。")
            actor = (
                get_user_model()
                .objects.filter(pk=options["actor_id"], is_active=True, is_superuser=True)
                .first()
            )
            if not actor:
                raise CommandError("修复操作人必须是有效的系统管理员。")
            try:
                result = repair_binding(
                    options["binding_id"],
                    signature=options["expected_signature"],
                    price_basis=options["price_basis"],
                    actor=actor,
                )
            except (ValidationError, TransferProductBinding.DoesNotExist) as exc:
                raise CommandError("；".join(getattr(exc, "messages", [str(exc)]))) from exc
            self.stdout.write(json.dumps(result, ensure_ascii=False, default=str))
            return
        rows = TransferProductBinding.objects.select_related(
            "source_product", "target_product"
        ).order_by("pk")
        if options["binding_id"]:
            rows = rows.filter(pk=options["binding_id"])
        if options["target_product"]:
            rows = rows.filter(target_product_id=options["target_product"])
        for binding in rows.iterator():
            self.stdout.write(
                json.dumps(
                    {
                        "binding_id": binding.pk,
                        **report_binding(binding, price_basis=options["price_basis"]),
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
