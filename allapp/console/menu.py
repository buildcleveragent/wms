"""Build the two-level WMS console menu from accessible registered destinations."""

from dataclasses import dataclass, field

from django.contrib import admin
from django.urls import NoReverseMatch, get_script_prefix, resolve, reverse

from allapp.accounts.access import AccessScope
from allapp.inventory.daily_close_review import can_view as can_view_daily_close
from allapp.outbound.authz import can_view_task_board
from allapp.tasking.views_console import TASK_TYPE_SLUG_TO_URL

from .views_billing import BillingOverviewView
from .views_salemini import can_view_catalog


@dataclass
class RibbonAction:
    label: str
    url_name: str
    icon: str = ""


@dataclass
class RibbonGroup:
    label: str
    actions: list[RibbonAction] = field(default_factory=list)


@dataclass
class RibbonTab:
    label: str
    groups: list[RibbonGroup] = field(default_factory=list)


RIBBON_TABS: list[RibbonTab] = [
    RibbonTab(
        label="任务",
        groups=[
            RibbonGroup(
                label="操作",
                actions=[
                    RibbonAction("任务列表", "admin:tasking_wmstask_changelist", "📋"),
                    RibbonAction("任务明细行", "admin:tasking_wmstaskline_changelist", "📑"),
                    RibbonAction("收货任务", "tasking_console:task_receive_list", "📥"),
                    RibbonAction("上架任务", "tasking_console:task_putaway_list", "📦"),
                    RibbonAction("拣货任务", "tasking_console:task_pick_list", "📑"),
                    RibbonAction("复核任务", "tasking_console:task_review_list", "🧮"),
                    RibbonAction("打包任务", "tasking_console:task_pack_list", "🎁"),
                    RibbonAction("发运任务", "tasking_console:task_dispatch_list", "🚚"),
                    RibbonAction("扫描日志(Admin)", "admin:tasking_taskscanlog_changelist", "🧾"),
                ],
            ),
        ],
    ),
    RibbonTab(
        label="入库",
        groups=[
            RibbonGroup(
                label="单据",
                actions=[
                    RibbonAction("入库单", "admin:inbound_inboundorder_changelist", "📥"),
                    RibbonAction("入库单行", "admin:inbound_inboundorderline_changelist", "📄"),
                    RibbonAction("收货单", "admin:inbound_inboundreceipt_changelist", "🧾"),
                    RibbonAction("收货单行", "admin:inbound_inboundreceiptline_changelist", "🧾"),
                ],
            ),
        ],
    ),
    RibbonTab(
        label="出库",
        groups=[
            RibbonGroup(
                label="单据",
                actions=[
                    RibbonAction("出库单", "admin:outbound_outboundorder_changelist", "📤"),
                    RibbonAction("出库单行", "admin:outbound_outboundorderline_changelist", "📄"),
                    RibbonAction("出库扩展", "admin:outbound_outboundorderextra_changelist", "🧩"),
                ],
            ),
        ],
    ),
    RibbonTab(
        label="库存/盘点",
        groups=[
            RibbonGroup(
                label="库存",
                actions=[
                    RibbonAction("库存日结与差异复核", "console:inventory_daily_close_list", "📋"),
                    RibbonAction("现存量", "admin:inventory_inventorydetail_changelist", "📊"),
                    RibbonAction(
                        "交易流水", "admin:inventory_inventorytransaction_changelist", "🔁"
                    ),
                    RibbonAction("过账分录", "admin:inventory_postingjournal_changelist", "🧾"),
                    RibbonAction("库存汇总", "admin:inventory_inventorysummary_changelist", "📈"),
                ],
            ),
            RibbonGroup(
                label="盘点",
                actions=[
                    RibbonAction("差异单", "admin:inventory_reviewdifference_changelist", "🧮"),
                    RibbonAction(
                        "差异单行", "admin:inventory_reviewdifferenceline_changelist", "🧮"
                    ),
                ],
            ),
        ],
    ),
    RibbonTab(
        label="基础资料",
        groups=[
            RibbonGroup(
                label="商品&单位",
                actions=[
                    RibbonAction("商品", "admin:products_product_changelist", "🏷️"),
                    RibbonAction("计量单位", "admin:products_productuom_changelist", "📏"),
                    RibbonAction("包装", "admin:products_productpackage_changelist", "📦"),
                ],
            ),
            RibbonGroup(
                label="库网格",
                actions=[
                    RibbonAction("仓库", "admin:locations_warehouse_changelist", "🏭"),
                    RibbonAction("库位", "admin:locations_location_changelist", "📍"),
                    RibbonAction("容器", "admin:locations_container_changelist", "🧰"),
                ],
            ),
            RibbonGroup(
                label="业务伙伴",
                actions=[
                    RibbonAction("货主", "admin:baseinfo_owner_changelist", "👑"),
                    RibbonAction("客户", "admin:baseinfo_customer_changelist", "🤝"),
                    RibbonAction("供应商", "admin:baseinfo_supplier_changelist", "🏢"),
                    RibbonAction("司机", "admin:baseinfo_driver_changelist", "🧑‍🔧"),
                    RibbonAction("车辆", "admin:baseinfo_vehicle_changelist", "🚚"),
                ],
            ),
        ],
    ),
    RibbonTab(
        label="商城",
        groups=[
            RibbonGroup(
                label="小程序运营",
                actions=[
                    RibbonAction(
                        "商城上架",
                        "console:sale_mini_product_listing",
                        "🛒",
                    ),
                    RibbonAction(
                        "上架配置(Admin)", "admin:salesapp_saleproductconfig_changelist", "🏷️"
                    ),
                    RibbonAction("小程序用户", "admin:salesapp_miniprogramuser_changelist", "👤"),
                    RibbonAction("首页横幅", "admin:salesapp_saleminibanner_changelist", "🖼️"),
                ],
            ),
        ],
    ),
    RibbonTab(
        label="计费",
        groups=[
            RibbonGroup(
                label="规则/账单",
                actions=[
                    RibbonAction(
                        "计费总览",
                        "console:billing_overview",
                        "💳",
                    ),
                    RibbonAction("计费规则", "admin:billing_billingrule_changelist", "⚙️"),
                    RibbonAction("规则阶梯", "admin:billing_billingruletier_changelist", "🪜"),
                    RibbonAction("计费期间", "admin:billing_billingperiod_changelist", "🗓️"),
                    RibbonAction("计费事件", "admin:billing_billingevent_changelist", "📌"),
                    RibbonAction("计提", "admin:billing_billingaccrual_changelist", "🧮"),
                    RibbonAction("账单", "admin:billing_bill_changelist", "🧾"),
                    RibbonAction("账单行", "admin:billing_billline_changelist", "📄"),
                ],
            ),
        ],
    ),
    RibbonTab(
        label="报表",
        groups=[
            RibbonGroup(
                label="常用",
                actions=[
                    RibbonAction("库存报表", "admin:inventory_inventorydetail_changelist", "📊"),
                    RibbonAction(
                        "出入库汇总", "admin:inventory_inventorytransaction_changelist", "📈"
                    ),
                ],
            ),
        ],
    ),
    RibbonTab(
        label="系统",
        groups=[
            RibbonGroup(
                label="用户与权限",
                actions=[
                    RibbonAction("Admin 首页", "admin:index", "⚙️"),
                    RibbonAction("用户", "admin:accounts_user_changelist", "👤"),
                    RibbonAction("用户组", "admin:auth_group_changelist", "👥"),
                ],
            ),
        ],
    ),
]


def _available_destinations(request):
    user = request.user
    console_routes = set()
    scope = AccessScope.for_request(request)
    if can_view_daily_close(user, scope=scope):
        console_routes.add("console:inventory_daily_close_list")
    if can_view_task_board(user, scope=scope):
        console_routes.update(TASK_TYPE_SLUG_TO_URL.values())
    if BillingOverviewView.can_view_page(user):
        console_routes.add("console:billing_overview")
    if can_view_catalog(user):
        console_routes.add("console:sale_mini_product_listing")

    staff = admin.site.has_permission(request)
    names = {
        action.url_name for tab in RIBBON_TABS for group in tab.groups for action in group.actions
    }
    urls = {}
    for name in names:
        model_list = staff and name.startswith("admin:") and name.endswith("_changelist")
        if not model_list and name not in console_routes:
            continue
        try:
            url = reverse(name)
        except NoReverseMatch:
            continue
        if model_list:
            path_info = "/" + url.removeprefix(get_script_prefix())
            model_admin = getattr(resolve(path_info).func, "model_admin", None)
            if model_admin is None or not model_admin.has_view_or_change_permission(request):
                continue
        urls[name] = url

    # Most staff already have a verified list destination. Staff with add-only
    # permissions or access to an unlisted module can still use the Admin index.
    if staff and (
        any(name.startswith("admin:") for name in urls)
        or user.get_all_permissions()
        and any(
            model["admin_url"] or model["add_url"]
            for app in admin.site.get_app_list(request)
            for model in app["models"]
        )
    ):
        urls["admin:index"] = reverse("admin:index")
    return urls


def build_ribbon(request) -> dict:
    """Return tabs with accessible destinations and the tab matching the current page."""
    user = request.user
    visible_tabs = []
    active_index = 0
    if not user.is_authenticated or not user.is_active:
        return {"tabs": visible_tabs, "active_index": active_index}

    destinations = _available_destinations(request)
    longest_match = 0
    for tab in RIBBON_TABS:
        groups = []
        for group in tab.groups:
            actions = []
            for action in group.actions:
                url = destinations.get(action.url_name)
                if url is None:
                    continue
                actions.append({"label": action.label, "url": url, "icon": action.icon or ""})
                if request.path.startswith(url) and len(url) > longest_match:
                    active_index = len(visible_tabs)
                    longest_match = len(url)
            if actions:
                groups.append({"label": group.label, "actions": actions})
        if groups:
            visible_tabs.append({"label": tab.label, "groups": groups})
    return {"tabs": visible_tabs, "active_index": active_index}
