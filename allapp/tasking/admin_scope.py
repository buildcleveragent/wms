"""Task-derived boundaries shared by child-model admin entry points."""

from django.core.exceptions import PermissionDenied, ValidationError

from allapp.accounts.access import AccessScope

from .models import WmsTask, WmsTaskLine


class TaskChildAdminScopeMixin:
    def _tasks(self, request):
        return AccessScope.for_user(request.user).filter_queryset(
            WmsTask.objects.all(), owner_field="owner_id", warehouse_field="warehouse_id"
        )

    def get_queryset(self, request):
        return super().get_queryset(request).filter(task_id__in=self._tasks(request).values("pk"))

    def has_add_permission(self, request):
        return AccessScope.for_user(request.user).is_valid and super().has_add_permission(request)

    def _in_scope(self, request, obj):
        return obj is None or self._tasks(request).filter(pk=obj.task_id).exists()

    def has_view_permission(self, request, obj=None):
        return self._in_scope(request, obj) and super().has_view_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._in_scope(request, obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._in_scope(request, obj) and super().has_delete_permission(request, obj)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "task":
            kwargs["queryset"] = self._tasks(request)
        elif db_field.name in ("line", "task_line"):
            kwargs["queryset"] = WmsTaskLine.objects.filter(task__in=self._tasks(request))
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def _validate_values(self, request, values):
        task = values.get("task")
        if not task or not self._tasks(request).filter(pk=task.pk).exists():
            raise ValidationError("任务不在当前用户授权范围内。")
        for field in ("owner", "warehouse"):
            value = values.get(field)
            if value and value.pk != getattr(task, f"{field}_id"):
                raise ValidationError("日志归属必须与任务一致。")
        line = values.get("line") or values.get("task_line")
        if line and line.task_id != task.pk:
            raise ValidationError("任务行不属于所选任务。")
        product = values.get("product")
        if product and product.owner_id != task.owner_id:
            raise ValidationError("商品不属于任务货主。")
        for field in ("from_location", "to_location", "location"):
            location = values.get(field)
            if location and location.warehouse_id != task.warehouse_id:
                raise ValidationError("库位不属于任务仓库。")

    def get_form(self, request, obj=None, **kwargs):
        base_form = super().get_form(request, obj, **kwargs)
        model_admin = self

        class ScopedForm(base_form):
            def clean(self):
                values = super().clean()
                # Readonly task fields are absent from cleaned_data on count tasks.
                effective = {"task": getattr(self.instance, "task", None), **values}
                model_admin._validate_values(request, effective)
                return values

        return ScopedForm

    def save_model(self, request, obj, form, change):
        try:
            self._validate_values(
                request,
                {
                    name: getattr(obj, name, None)
                    for name in (
                        "task",
                        "line",
                        "task_line",
                        "product",
                        "from_location",
                        "to_location",
                        "location",
                        "owner",
                        "warehouse",
                    )
                },
            )
        except ValidationError as exc:
            raise PermissionDenied("任务关联对象不在授权范围内。") from exc
        return super().save_model(request, obj, form, change)
