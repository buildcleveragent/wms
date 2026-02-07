# forms_mixins.py
from django import forms

class DependentSelectMixin:
    """
    通用下拉依赖：
    在子类里设置 self.dependencies = {
        "child_field": {
            "source": "parent_field",
            "model": ModelClass,
            "filter": {"外键字段_id或其它": "{parent_field_id}"}
        },
        ...
    }
    注意：filter 的值里占位符写成 {<source>_id} 或 {<source>} 都行，
         会自动替换为上游值（一般用 *_id 比较稳）。
    """

    dependencies: dict[str, dict] = {}

    def _get_source_value(self, name: str):
        # 依次从 data / initial / instance 读取
        return (
            self.data.get(name)
            or self.initial.get(name)
            or getattr(self.instance, f"{name}_id", None)
            or getattr(self.instance, name, None)
        )

    def _resolve_filter(self, mapping: dict, source: str, value):
        # 支持形如 {"CarrierCompany_id": "{carrierCompany_id}"} 的占位替换
        resolved = {}
        for k, v in mapping.items():
            if isinstance(v, str) and v.startswith("{") and v.endswith("}"):
                key = v.strip("{}")
                if key == f"{source}_id":
                    resolved[k] = value
                elif key == source:
                    resolved[k] = value  # 万一你传的是实例也能兼容
                else:
                    # 允许用其它占位，按需扩展
                    resolved[k] = value
            else:
                resolved[k] = v
        return resolved

    def _apply_dependencies(self):
        for child, cfg in self.dependencies.items():
            src = cfg["source"]
            model = cfg["model"]
            filtr = cfg["filter"]

            src_val = self._get_source_value(src)
            if src_val:
                # 注意：大多数场景 src_val 是 id（字符串/数字）
                # 如果你传进来的是实例，也能工作（Django 允许）
                resolved = self._resolve_filter(filtr, src, src_val)
                self.fields[child].queryset = model.objects.filter(**resolved)
            else:
                self.fields[child].queryset = model.objects.none()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)  # 调用你子类的父类 __init__
        self._apply_dependencies()
