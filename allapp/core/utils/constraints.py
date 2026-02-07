#constraints.py
from django.db.models import Q, CheckConstraint

def not_empty_checks(*field_names: str, prefix: str) -> list[CheckConstraint]:
    cs = []
    for f in field_names:
        name = f"{prefix}_{f}_ne"
        # 你的项目有“约束名≤30”限制，必要时自行截断/加哈希
        if len(name) > 30:
            name = name[:30]
        cs.append(CheckConstraint(check=~Q(**{f: ""}), name=name))
    return cs
