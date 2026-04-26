# allapp/locations/admin.py
from decimal import Decimal, ROUND_HALF_UP
from django.contrib import admin
from django.db.models import Count
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from .models import Warehouse, Subwarehouse,Location,Container

class FloorNoListFilter(admin.SimpleListFilter):
    """按楼层筛选：显示“有某楼层库位”的子仓"""
    title = _("楼层")
    parameter_name = "floor"

    def lookups(self, request, model_admin):
        # 可按需要扩展楼层范围
        return [(str(i), _(f"{i}层")) for i in range(1, 10)]

    def queryset(self, request, queryset):
        val = self.value()
        if val:
            try:
                floor = int(val)
            except ValueError:
                return queryset
            # 仅保留“有该楼层库位”的子仓
            return queryset.filter(locations__floor_no=floor).distinct()
        return queryset

##---------- Admins ----------
@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    fields = ("code", "name")
    list_display = ("code", "name")
    search_fields = ("code", "name")
    ordering = ("code",)

@admin.register(Subwarehouse)
class SubwarehouseAdmin(admin.ModelAdmin):
    fields = ("warehouse", "code", "name",  "floor_no",)
    list_display = ("warehouse", "code", "name",  "floor_no", "loc_cnt", "locations_link","is_active",)
    list_filter = ("warehouse", "is_active", FloorNoListFilter)
    search_fields = ("code", "name","floor_no",)
    ordering = ("code",)
    list_per_page = 50

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # 统计每个子仓下库位数量（依赖 Location.subwarehouse.related_name='locations'）
        return qs.annotate(_loc_cnt=Count("locations"))

    @admin.display(description=_("库位数"), ordering="_loc_cnt")
    def loc_cnt(self, obj: Subwarehouse):
        return obj._loc_cnt

    @admin.display(description=_("库位列表"))
    def locations_link(self, obj: Subwarehouse):
        url = (
            reverse("admin:locations_location_changelist")
            + f"?subwarehouse__id__exact={obj.pk}"
        )
        return format_html('<a href="{}">{}</a>', url, _("查看该子仓库位"))



@admin.action(description="批量设为禁用")
def mark_disabled(modeladmin, request, queryset):
    queryset.update(is_disabled=True)

@admin.action(description="批量取消禁用")
def unmark_disabled(modeladmin, request, queryset):
    queryset.update(is_disabled=False)

@admin.action(description="批量冻结")
def mark_frozen(modeladmin, request, queryset):
    queryset.update(is_frozen=True)

@admin.action(description="批量解冻")
def unmark_frozen(modeladmin, request, queryset):
    queryset.update(is_frozen=False)

@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = (
        "warehouse", "code", "name", "zone_type", "subwarehouse","level_code","col_no", "slot_no", "barcode","is_disabled", "is_frozen")
    list_filter = (
        "warehouse", "subwarehouse", "zone_type", "is_disabled", "is_frozen",
    )
    search_fields = ("code", "name", "barcode", )
    ordering = ("subwarehouse", "zone_type", "code")
    readonly_fields = ("warehouse", "subwarehouse","level_code","col_no","slot_no", "barcode", )

    fieldsets = (
        (None, {
            'fields': (
                "code", "name", "zone_type", ("warehouse", "subwarehouse"),"level_code","col_no", "slot_no", "barcode", )
        }),
        ("存储能力", {
            'fields': (("max_volume_m3", "max_weight_kg"))
        }),
        ("状态", {
            'fields': (
                "is_disabled", "is_frozen"
            )
        }),
        ("限制放置商品类别", {
            'fields': ("product_categories",)
        }),
        ("指引", {
            'fields': ("batch_guide",)
        }),
    )

    def save_model(self, request, obj, form, change):
        """
        Override save to ensure that `clean()` is called before saving.
        This will ensure the `rack_code`, `rack_level_code`, `col_no`, `slot_no` are set correctly
        based on the `code` field.
        """
        obj.clean()  # Ensure the code field is validated and other fields are populated
        super().save_model(request, obj, form, change)


@admin.register(Container)
class ContainerAdmin(admin.ModelAdmin):
    # —— 列表展示 —— #
    list_display = (
        "id", "container_no", "container_type", "owner", "warehouse",
        "location", "parent",
        "volume_l", "gross_limit_kg_disp",
    )
    list_display_links = ("container_no",)
    ordering = ("-id",)
    list_per_page = 50

    # —— 过滤/检索 —— #
    list_filter = (
        "container_type",
        "owner",
        ("location", admin.RelatedOnlyFieldListFilter),
        ("parent", admin.RelatedOnlyFieldListFilter),
    )
    search_fields = ("container_no", "id")
    search_help_text = "按容器号或ID搜索"

    # —— 性能优化 —— #
    list_select_related = ("owner", "warehouse", "location", "parent")

    # —— 外键输入（避免依赖对方 Admin 注册导致 E039） —— #
    raw_id_fields = ("owner", "warehouse", "location", "parent")

    # —— 表单分组 —— #
    fieldsets = (
        ("基础信息", {
            "fields": (("container_no", "container_type"),
                       ("owner", "warehouse"),
                       ("location", "parent")),
        }),
        ("尺寸/载重", {
            "classes": ("collapse",),
            "fields": (
                ("length_cm", "width_cm", "height_cm"),
                ("tare_kg", "max_gross_kg", ),
            ),
        }),
    )

    # —— 只读控制：创建后不允许跨租户/跨仓变更 —— #
    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ("owner", "warehouse")
        return ()

    # —— 计算列：体积/上限 —— #
    @admin.display(description="体积(L)")
    def volume_l(self, obj: Container):
        try:
            if obj.length_cm and obj.width_cm and obj.height_cm:
                # cm^3 → L：/1000；统一四舍五入两位
                vol = (Decimal(obj.length_cm) *
                       Decimal(obj.width_cm) *
                       Decimal(obj.height_cm)) / Decimal("1000")
                return vol.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except Exception:
            pass
        return ""

    @admin.display(description="理论总重上限(kg)")
    def gross_limit_kg_disp(self, obj: Container):
        g = obj.gross_limit_kg()
        return g.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP) if g is not None else ""

    # —— 批量动作 —— #
    actions = ["action_clear_location", "action_unparent"]

    @admin.action(description="批量清空当前位置")
    def action_clear_location(self, request, queryset):
        n = queryset.update(location=None)
        self.message_user(request, f"已清空 {n} 个容器的当前位置。")

    @admin.action(description="批量取消父容器关联")
    def action_unparent(self, request, queryset):
        n = queryset.update(parent=None)
        self.message_user(request, f"已取消 {n} 个容器的父子关系。")
