# ===============================
# FILE: allapp/console/menu.py
# 说明：管理员“可用即能点”的双层菜单（Tab + Actions）。
#       链接尽量采用 Django Admin 命名路由，保证开箱即用；
#       未能反解（模型未注册 Admin）的动作将自动隐藏。
# ===============================
from dataclasses import dataclass, field
from typing import List, Sequence, Optional
from django.urls import reverse, NoReverseMatch

# —— 配置数据结构 —— #
@dataclass
class RibbonAction:
    label: str
    url_name: Optional[str] = None     # Django 命名路由（如 admin:app_model_changelist / tasking_console:task_list）
    icon: Optional[str] = None         # emoji 轻量图标
    perms_any: Sequence[str] = ()      # 任一权限满足则显示
    groups_any: Sequence[str] = ()     # 任一分组命中则显示（可选）

@dataclass
class RibbonGroup:
    label: str
    actions: List[RibbonAction] = field(default_factory=list)
    perms_any: Sequence[str] = ()
    groups_any: Sequence[str] = ()

@dataclass
class RibbonTab:
    label: str
    groups: List[RibbonGroup] = field(default_factory=list)
    perms_any: Sequence[str] = ()
    groups_any: Sequence[str] = ()


# —— 常用的 Admin 命名路由速查 —— #
#  列表页：  admin:<app_label>_<model_name>_changelist
#  新增页：  admin:<app_label>_<model_name>_add
#  主页：    admin:index
#
#  例：WmsTask（app=tasking，model=WmsTask）→ admin:tasking_wmstask_changelist
#  注：若某模型未注册到 Admin，reverse 会失败；下方构建函数会自动隐藏该动作。

# —— 这里列出“管理员”常用功能（Tab/Group/Action） —— #
RIBBON_TABS: List[RibbonTab] = [
    RibbonTab(
        label="任务",
        groups=[
            RibbonGroup(
                label="操作",
                actions=[
                    # RibbonAction("任务控制台", "tasking_console:task_list", "🧭", perms_any=["tasking.view_wmstask"]),
                    # RibbonAction("作业任务行", "tasking_console:task_line_work_list", "🧑\u200d🏭"),
                    RibbonAction("任务列表", "admin:tasking_wmstask_changelist", "📋"),
                    RibbonAction("任务明细行", "admin:tasking_wmstaskline_changelist", "📑"),
                    # 细分任务看板默认向所有登录用户开放（视图自身会再做权限控制）。
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
                    RibbonAction("现存量", "admin:inventory_inventorydetail_changelist", "📊"),
                    RibbonAction("交易流水", "admin:inventory_inventorytransaction_changelist", "🔁"),
                    RibbonAction("过账分录", "admin:inventory_postingjournal_changelist", "🧾"),
                    RibbonAction("库存汇总", "admin:inventory_inventorysummary_changelist", "📈"),
                ],
            ),
            RibbonGroup(
                label="盘点",
                actions=[
                    RibbonAction("差异单", "admin:inventory_reviewdifference_changelist", "🧮"),
                    RibbonAction("差异单行", "admin:inventory_reviewdifferenceline_changelist", "🧮"),
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
        label="计费",
        groups=[
            RibbonGroup(
                label="规则/账单",
                actions=[
                    RibbonAction(
                        "计费总览",
                        "console:billing_overview",
                        "💳",
                        perms_any=[
                            "billing.view_bill",
                            "billing.view_billingperiod",
                            "billing.view_billingaccrual",
                        ],
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
                    # 你可以把这些替换为实际命名路由（如 reports:xxx）
                    RibbonAction("库存报表", "admin:inventory_inventorydetail_changelist", "📊"),
                    RibbonAction("出入库汇总", "admin:inventory_inventorytransaction_changelist", "📈"),
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
                    RibbonAction("用户", "admin:auth_user_changelist", "👤"),
                    RibbonAction("用户组", "admin:auth_group_changelist", "👥"),
                ],
            ),
            # 如需站点、日志等：
            # RibbonGroup(
            #     label="运维",
            #     actions=[
            #         RibbonAction("站点", "admin:sites_site_changelist", "🛰️"),
            #         RibbonAction("Admin 日志", "admin:admin_logentry_changelist", "📝"),
            #     ],
            # ),
        ],
    ),
]


# —— 权限/分组判断 —— #
def _can_see(user, perms_any: Sequence[str], groups_any: Sequence[str]) -> bool:
    # 管理员直接可见；否则按权限/分组判断
    if getattr(user, "is_superuser", False):
        return True
    if groups_any:
        if any(g.name in groups_any for g in user.groups.all()):
            return True
    if perms_any:
        return any(user.has_perm(p) for p in perms_any)
    return True


# —— 反解命名路由；若失败返回 None（用于隐藏动作） —— #
def _resolve_url(url_name: Optional[str]) -> Optional[str]:
    if not url_name:
        return None
    try:
        return reverse(url_name)
    except NoReverseMatch:
        return None


# —— 构建用于模板的“可见菜单”数据 —— #
def build_ribbon(user, request_path: str) -> dict:
    visible_tabs = []

    for tab in RIBBON_TABS:
        if not _can_see(user, tab.perms_any, tab.groups_any):
            continue

        vg = []
        for grp in tab.groups:
            if not _can_see(user, grp.perms_any, grp.groups_any):
                continue

            va = []
            for act in grp.actions:
                if not _can_see(user, act.perms_any, act.groups_any):
                    continue
                url = _resolve_url(act.url_name)
                if not url:
                    # 反解失败（如模型未注册到 Admin），不显示该动作，避免出现“#”死链
                    continue
                va.append({"label": act.label, "url": url, "icon": act.icon or ""})

            if va:
                vg.append({"label": grp.label, "actions": va})

        if vg:
            visible_tabs.append({"label": tab.label, "groups": vg})

    # 计算 active tab（若当前 path 前缀命中其中任一动作的 URL，则激活对应 tab，否则默认第 0 个）
    active_idx = 0
    for i, t in enumerate(visible_tabs):
        matched = False
        for g in t["groups"]:
            for a in g["actions"]:
                if request_path and a["url"] != "#" and request_path.startswith(a["url"]):
                    active_idx = i
                    matched = True
                    break
            if matched:
                break
        if matched:
            break

    return {"tabs": visible_tabs, "active_index": active_idx}
