from __future__ import annotations

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from allapp.accounts.access import AccessScope
from allapp.tasking import counting
from allapp.tasking.models import TaskAssignment, WmsTask, WmsTaskLine


class CountTaskCreateSerializer(serializers.Serializer):
    owner_id = serializers.IntegerField()
    warehouse_id = serializers.IntegerField()
    scope = serializers.ChoiceField(
        choices=["ALL", "ZONE", "LOC", "SKU"], default="ALL"
    )
    subwarehouse_id = serializers.IntegerField(required=False, allow_null=True)
    zone_type = serializers.IntegerField(required=False, allow_null=True)
    location_id = serializers.IntegerField(required=False, allow_null=True)
    location_prefix = serializers.CharField(required=False, allow_blank=True)
    product_id = serializers.IntegerField(required=False, allow_null=True)
    batch_no = serializers.CharField(required=False, allow_blank=True)
    exclude_zero_onhand = serializers.BooleanField(default=True)
    max_lines = serializers.IntegerField(default=1000, min_value=1, max_value=10000)
    blind = serializers.BooleanField(default=True)
    recount_threshold = serializers.DecimalField(
        max_digits=14, decimal_places=4, default=0
    )
    task_remark = serializers.CharField(required=False, allow_blank=True)


class CountRecordSerializer(serializers.Serializer):
    line_id = serializers.IntegerField()
    qty_counted = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=0)
    client_seq = serializers.CharField(max_length=100)
    barcode = serializers.CharField(required=False, allow_blank=True, max_length=128)
    device_id = serializers.CharField(required=False, allow_blank=True, max_length=64)


class CountDecisionSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=200)


class CountTaskSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    round_no = serializers.IntegerField(
        source="counttaskextra.round_no", read_only=True
    )
    blind = serializers.BooleanField(source="counttaskextra.blind", read_only=True)
    scope = serializers.CharField(source="counttaskextra.scope", read_only=True)
    has_active_assignment = serializers.SerializerMethodField()
    assigned_to_me = serializers.SerializerMethodField()
    can_record = serializers.SerializerMethodField()

    class Meta:
        model = WmsTask
        fields = [
            "id",
            "task_no",
            "task_group_no",
            "status",
            "review_status",
            "posting_status",
            "owner_id",
            "owner_name",
            "warehouse_id",
            "warehouse_name",
            "scope",
            "blind",
            "round_no",
            "has_active_assignment",
            "assigned_to_me",
            "can_record",
            "remark",
            "released_at",
            "finished_at",
        ]

    def _active_assignments(self, obj):
        return obj.assignments.filter(finished_at__isnull=True, line__isnull=True)

    def get_has_active_assignment(self, obj):
        return self._active_assignments(obj).exists()

    def get_assigned_to_me(self, obj):
        request = self.context.get("request")
        return bool(
            request
            and self._active_assignments(obj).filter(assignee=request.user).exists()
        )

    def get_can_record(self, obj):
        request = self.context.get("request")
        if not request:
            return False
        if request.user.is_superuser or request.user.has_perm(
            "tasking.taskconfirm_as_wh_manager"
        ):
            return True
        return self._active_assignments(obj).filter(assignee=request.user).exists()


class CountLineSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_code = serializers.CharField(source="product.code", read_only=True)
    unit_barcode = serializers.CharField(source="product.unit_barcode", read_only=True)
    carton_barcode = serializers.CharField(
        source="product.carton_barcode", read_only=True
    )
    gtin = serializers.CharField(source="product.gtin", read_only=True)
    location_code = serializers.CharField(source="from_location.code", read_only=True)
    lot_no = serializers.CharField(source="countlineextra.lot_no", read_only=True)
    exp_date = serializers.DateField(source="countlineextra.exp_date", read_only=True)
    qty_counted = serializers.DecimalField(
        source="countlineextra.qty_counted",
        max_digits=18,
        decimal_places=4,
        read_only=True,
    )
    qty_book = serializers.DecimalField(
        source="countlineextra.qty_book",
        max_digits=18,
        decimal_places=4,
        read_only=True,
    )
    qty_diff = serializers.DecimalField(
        source="countlineextra.qty_diff",
        max_digits=18,
        decimal_places=4,
        read_only=True,
    )
    count_status = serializers.CharField(
        source="countlineextra.count_status", read_only=True
    )
    countorder = serializers.CharField(
        source="countlineextra.countorder", read_only=True
    )

    class Meta:
        model = WmsTaskLine
        fields = [
            "id",
            "task_id",
            "product_id",
            "product_sku",
            "product_name",
            "product_code",
            "unit_barcode",
            "carton_barcode",
            "gtin",
            "from_location_id",
            "location_code",
            "lot_no",
            "exp_date",
            "qty_counted",
            "qty_book",
            "qty_diff",
            "count_status",
            "countorder",
            "status",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        task = instance.task
        manager = bool(
            request
            and (
                request.user.is_superuser
                or request.user.has_perm("tasking.taskconfirm_as_wh_manager")
            )
        )
        blind = getattr(getattr(task, "counttaskextra", None), "blind", True)
        if (
            blind
            and task.status not in {WmsTask.Status.COMPLETED, WmsTask.Status.CANCELLED}
            and not manager
        ):
            data.pop("qty_book", None)
            data.pop("qty_diff", None)
        return data


class CountTaskViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CountTaskSerializer

    def get_queryset(self):
        qs = WmsTask.objects.filter(task_type=WmsTask.TaskType.COUNT).select_related(
            "owner", "warehouse", "counttaskextra"
        )
        qs = AccessScope.for_user(self.request.user).filter_queryset(qs)
        if not self._manager():
            active_assignments = TaskAssignment.objects.filter(
                task_id=OuterRef("pk"), finished_at__isnull=True
            )
            own_assignment = active_assignments.filter(assignee=self.request.user)
            qs = qs.annotate(
                has_active_assignment=Exists(active_assignments),
                is_my_assignment=Exists(own_assignment),
            ).filter(
                Q(is_my_assignment=True)
                | Q(status=WmsTask.Status.RELEASED, has_active_assignment=False)
            )
        requested = self.request.query_params.getlist("status")
        if requested:
            qs = qs.filter(status__in=requested)
        elif self.action == "list":
            qs = qs.filter(
                Q(status__in=[WmsTask.Status.RELEASED, WmsTask.Status.IN_PROGRESS])
                | Q(
                    status=WmsTask.Status.COMPLETED,
                    review_status=WmsTask.ReviewStatus.PENDING,
                )
            )
        return qs.order_by("-id")

    def _task(self, pk, *, for_update=False):
        qs = self.get_queryset()
        if for_update:
            qs = qs.select_for_update()
        return get_object_or_404(qs, pk=pk)

    def _manager(self):
        user = self.request.user
        return user.is_superuser or user.has_perm("tasking.taskconfirm_as_wh_manager")

    def _run(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DjangoPermissionDenied as exc:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(str(exc)) from exc
        except DjangoValidationError as exc:
            detail = (
                getattr(exc, "message_dict", None)
                or getattr(exc, "messages", None)
                or str(exc)
            )
            raise serializers.ValidationError(detail) from exc

    def list(self, request):
        data = CountTaskSerializer(
            self.get_queryset(), many=True, context={"request": request}
        ).data
        return Response(data)

    def retrieve(self, request, pk=None):
        return Response(
            CountTaskSerializer(self._task(pk), context={"request": request}).data
        )

    def create(self, request):
        if not self._manager():
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("无盘点创建权限。")
        serializer = CountTaskCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        scope = AccessScope.for_user(request.user)
        if not scope.allows(values["owner_id"], values["warehouse_id"]):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("盘点范围超出当前用户的数据权限。")
        task, created, truncated = self._run(
            counting.create_count_task, created_by=request.user, **values
        )
        data = CountTaskSerializer(task, context={"request": request}).data
        data.update({"created_lines": created, "truncated": truncated})
        return Response(data, status=status.HTTP_201_CREATED)

    @action(methods=["get"], detail=True)
    def lines(self, request, pk=None):
        task = self._task(pk)
        qs = (
            WmsTaskLine.objects.filter(task=task)
            .select_related(
                "task",
                "task__counttaskextra",
                "product",
                "from_location",
                "countlineextra",
            )
            .order_by("id")
        )
        return Response(
            CountLineSerializer(qs, many=True, context={"request": request}).data
        )

    @action(methods=["post"], detail=True)
    def release(self, request, pk=None):
        self._task(pk)
        task = self._run(counting.release_count_task, int(pk), by_user=request.user)
        return Response(CountTaskSerializer(task, context={"request": request}).data)

    @action(methods=["post"], detail=True)
    def claim(self, request, pk=None):
        self._task(pk)
        assignment = self._run(counting.claim_count_task, int(pk), by_user=request.user)
        return Response({"detail": "claimed", "assignment_id": assignment.id})

    @action(methods=["post"], detail=True)
    def record(self, request, pk=None):
        self._task(pk)
        serializer = CountRecordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = self._run(
            counting.record_count,
            int(pk),
            by_user=request.user,
            source="PDA",
            **serializer.validated_data,
        )
        return Response(result)

    @action(methods=["post"], detail=True)
    def submit(self, request, pk=None):
        self._task(pk)
        return Response(
            self._run(counting.submit_count_task, int(pk), by_user=request.user)
        )

    @action(methods=["post"], detail=True)
    def approve(self, request, pk=None):
        self._task(pk)
        serializer = CountDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = self._run(
            counting.approve_count_task,
            int(pk),
            by_user=request.user,
            note=serializer.validated_data.get("note", ""),
        )
        return Response(CountTaskSerializer(task, context={"request": request}).data)

    @action(methods=["post"], detail=True)
    def reject(self, request, pk=None):
        self._task(pk)
        serializer = CountDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = self._run(
            counting.reject_count_task,
            int(pk),
            by_user=request.user,
            note=serializer.validated_data.get("note", ""),
        )
        return Response(CountTaskSerializer(task, context={"request": request}).data)

    @action(methods=["post"], detail=True)
    def post(self, request, pk=None):
        self._task(pk)
        serializer = CountDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            self._run(
                counting.post_count_task,
                int(pk),
                by_user=request.user,
                note=serializer.validated_data.get("note", ""),
            )
        )

    @action(methods=["post"], detail=True)
    def cancel(self, request, pk=None):
        self._task(pk)
        serializer = CountDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = self._run(
            counting.cancel_count_task,
            int(pk),
            by_user=request.user,
            note=serializer.validated_data.get("note", ""),
        )
        return Response(CountTaskSerializer(task, context={"request": request}).data)
