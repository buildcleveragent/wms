from __future__ import annotations

import re
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from allapp.products.models import Product, ProductCategory

# Ordered parent-first so every category can be validated and created with the
# normal ProductCategory model rules.
CATEGORY_SPECS = (
    ("01", "酒水", None, 10),
    ("TST-01-BAIJIU", "白酒", "01", 10),
    ("TST-01-BAIJIU-NONG", "浓香白酒", "TST-01-BAIJIU", 10),
    ("TST-01-BAIJIU-JIANG", "酱香白酒", "TST-01-BAIJIU", 20),
    ("TST-01-BAIJIU-OTHER", "其他白酒", "TST-01-BAIJIU", 30),
    ("TST-01-BEER", "啤酒", "01", 20),
    ("TST-01-BEER-CN", "国产啤酒", "TST-01-BEER", 10),
    ("TST-01-BEER-IMPORT", "进口啤酒", "TST-01-BEER", 20),
    ("TST-01-WINE", "葡萄酒", "01", 30),
    ("TST-01-WINE-RED", "红葡萄酒", "TST-01-WINE", 10),
    ("TST-01-WINE-WHITE", "白葡萄酒", "TST-01-WINE", 20),
    ("TST-01-WINE-OTHER", "其他葡萄酒", "TST-01-WINE", 30),
    ("02", "饮料", None, 20),
    ("TST-02-WATER", "水饮", "02", 10),
    ("TST-02-WATER-PLAIN", "饮用水", "TST-02-WATER", 10),
    ("TST-02-WATER-SODA", "碳酸饮料", "TST-02-WATER", 20),
    ("TST-02-WATER-JUICE", "果汁茶饮", "TST-02-WATER", 30),
    ("TST-02-DAIRY", "乳豆饮品", "02", 20),
    ("TST-02-DAIRY-SOY", "豆奶", "TST-02-DAIRY", 10),
    ("TST-02-DAIRY-MILK", "牛奶乳饮", "TST-02-DAIRY", 20),
    ("TST-02-DAIRY-PLANT", "植物饮料", "TST-02-DAIRY", 30),
    ("TST-02-INSTANT", "冲调饮品", "02", 30),
    ("TST-02-INSTANT-COFFEE", "咖啡", "TST-02-INSTANT", 10),
    ("TST-02-INSTANT-TEA", "茶叶", "TST-02-INSTANT", 20),
    ("TST-02-INSTANT-POWDER", "固体饮料", "TST-02-INSTANT", 30),
    ("03", "粮油", None, 30),
    ("MI", "米", "03", 10),
    ("ZZMI", "珍珠米", "MI", 10),
    ("SMMI", "丝苗米", "MI", 20),
    ("TST-03-RICE-OTHER", "其他大米", "MI", 30),
    ("MIAN", "面", "03", 20),
    ("TST-03-NOODLE-DRY", "挂面", "MIAN", 10),
    ("TST-03-NOODLE-FLOUR", "面粉", "MIAN", 20),
    ("TST-03-NOODLE-INSTANT", "方便食品", "MIAN", 30),
    ("TST-03-OIL", "食用油", "03", 30),
    ("TST-03-OIL-PEANUT", "花生油", "TST-03-OIL", 10),
    ("TST-03-OIL-BLEND", "调和油", "TST-03-OIL", 20),
    ("TST-03-OIL-OTHER", "其他食用油", "TST-03-OIL", 30),
    ("TST-03-SEASONING", "调味品", "03", 40),
    ("TST-03-SEASONING-SAUCE", "酱油醋", "TST-03-SEASONING", 10),
    ("TST-03-SEASONING-SALT", "盐糖", "TST-03-SEASONING", 20),
    ("TST-03-SEASONING-SPICE", "酱料香辛料", "TST-03-SEASONING", 30),
    ("04", "干杂", None, 40),
    ("TST-04-NUT", "坚果炒货", "04", 10),
    ("TST-04-NUT-NUT", "坚果", "TST-04-NUT", 10),
    ("TST-04-NUT-ROAST", "炒货", "TST-04-NUT", 20),
    ("TST-04-NUT-GIFT", "坚果礼盒", "TST-04-NUT", 30),
    ("TST-04-DRIED", "果干蜜饯", "04", 20),
    ("TST-04-DRIED-RAISIN", "葡萄干", "TST-04-DRIED", 10),
    ("TST-04-DRIED-FRUIT", "水果干", "TST-04-DRIED", 20),
    ("TST-04-DRIED-CANDIED", "蜜饯果脯", "TST-04-DRIED", 30),
    ("TST-04-SNACK", "休闲零食", "04", 30),
    ("TST-04-SNACK-BAKED", "饼干糕点", "TST-04-SNACK", 10),
    ("TST-04-SNACK-CANDY", "糖果巧克力", "TST-04-SNACK", 20),
    ("TST-04-SNACK-PUFFED", "膨化食品", "TST-04-SNACK", 30),
    ("TST-04-DRY", "南北干货", "04", 40),
    ("TST-04-DRY-MUSHROOM", "菌菇干货", "TST-04-DRY", 10),
    ("TST-04-DRY-BEAN", "豆类杂粮", "TST-04-DRY", 20),
    ("TST-04-DRY-VEGETABLE", "干菜", "TST-04-DRY", 30),
    ("05", "耗材", None, 50),
    ("TST-05-CATERING", "餐饮耗材", "05", 10),
    ("TST-05-CATERING-TABLEWARE", "一次性餐具", "TST-05-CATERING", 10),
    ("TST-05-CATERING-CUP", "杯碗吸管", "TST-05-CATERING", 20),
    ("TST-05-CATERING-FRESH", "保鲜用品", "TST-05-CATERING", 30),
    ("TST-05-CLEAN", "清洁耗材", "05", 20),
    ("TST-05-CLEAN-BAG", "垃圾袋", "TST-05-CLEAN", 10),
    ("TST-05-CLEAN-TOOL", "清洁工具", "TST-05-CLEAN", 20),
    ("TST-05-CLEAN-LABOR", "劳保用品", "TST-05-CLEAN", 30),
    ("06", "生鲜", None, 60),
    ("TST-06-EGG", "蛋品乳品", "06", 10),
    ("TST-06-EGG-CHICKEN", "鸡蛋", "TST-06-EGG", 10),
    ("TST-06-EGG-OTHER", "其他蛋品", "TST-06-EGG", 20),
    ("TST-06-EGG-MILK", "鲜奶", "TST-06-EGG", 30),
    ("TST-06-MEAT", "肉禽熟食", "06", 20),
    ("TST-06-MEAT-LIVESTOCK", "猪牛羊肉", "TST-06-MEAT", 10),
    ("TST-06-MEAT-POULTRY", "禽肉", "TST-06-MEAT", 20),
    ("TST-06-MEAT-CURED", "腊味熟食", "TST-06-MEAT", 30),
    ("TST-06-SEAFOOD", "水产海鲜", "06", 30),
    ("TST-06-SEAFOOD-FISH", "鱼类", "TST-06-SEAFOOD", 10),
    ("TST-06-SEAFOOD-SHELL", "虾蟹贝类", "TST-06-SEAFOOD", 20),
    ("TST-06-SEAFOOD-PROCESSED", "水产加工", "TST-06-SEAFOOD", 30),
    ("TST-06-PRODUCE", "蔬菜水果", "06", 40),
    ("TST-06-PRODUCE-VEGETABLE", "蔬菜", "TST-06-PRODUCE", 10),
    ("TST-06-PRODUCE-FRUIT", "水果", "TST-06-PRODUCE", 20),
    ("TST-06-PRODUCE-MUSHROOM", "鲜菌", "TST-06-PRODUCE", 30),
    ("TST-06-FROZEN", "冷冻食品", "06", 50),
    ("TST-06-FROZEN-MEAT", "冷冻肉食", "TST-06-FROZEN", 10),
    ("TST-06-FROZEN-PASTRY", "速冻面点", "TST-06-FROZEN", 20),
    ("TST-06-FROZEN-OTHER", "其他冷冻", "TST-06-FROZEN", 30),
    ("07", "日化", None, 70),
    ("TST-07-HOME", "家庭清洁", "07", 10),
    ("TST-07-HOME-LAUNDRY", "洗衣护理", "TST-07-HOME", 10),
    ("TST-07-HOME-KITCHEN", "厨卫清洁", "TST-07-HOME", 20),
    ("TST-07-HOME-ROOM", "居室清洁", "TST-07-HOME", 30),
    ("TST-07-PERSONAL", "个人护理", "07", 20),
    ("TST-07-PERSONAL-HAIR", "洗发护发", "TST-07-PERSONAL", 10),
    ("TST-07-PERSONAL-BATH", "沐浴清洁", "TST-07-PERSONAL", 20),
    ("TST-07-PERSONAL-ORAL", "口腔护理", "TST-07-PERSONAL", 30),
    ("TST-07-BEAUTY", "美妆护肤", "07", 30),
    ("TST-07-BEAUTY-FACE", "面部护理", "TST-07-BEAUTY", 10),
    ("TST-07-BEAUTY-BODY", "身体护理", "TST-07-BEAUTY", 20),
    ("TST-07-BEAUTY-MAKEUP", "美妆用品", "TST-07-BEAUTY", 30),
    ("08", "纸制品", None, 80),
    ("TST-08-LIFE", "生活用纸", "08", 10),
    ("TST-08-LIFE-TISSUE", "抽纸", "TST-08-LIFE", 10),
    ("TST-08-LIFE-ROLL", "卷纸", "TST-08-LIFE", 20),
    ("TST-08-LIFE-WET", "湿巾", "TST-08-LIFE", 30),
    ("TST-08-OFFICE", "办公纸品", "08", 20),
    ("TST-08-OFFICE-PRINT", "打印纸", "TST-08-OFFICE", 10),
    ("TST-08-OFFICE-LABEL", "标签纸", "TST-08-OFFICE", 20),
    ("TST-08-OFFICE-OTHER", "其他纸品", "TST-08-OFFICE", 30),
    ("09", "包装", None, 90),
    ("TST-09-FOOD", "食品包装", "09", 10),
    ("TST-09-FOOD-CAN", "包装罐", "TST-09-FOOD", 10),
    ("TST-09-FOOD-LID", "瓶盖", "TST-09-FOOD", 20),
    ("TST-09-FOOD-SEAL", "封口膜", "TST-09-FOOD", 30),
    ("TST-09-PLASTIC", "塑料包装", "09", 20),
    ("TST-09-PLASTIC-BAG", "包装袋", "TST-09-PLASTIC", 10),
    ("TST-09-PLASTIC-BOX", "塑料盒", "TST-09-PLASTIC", 20),
    ("TST-09-PLASTIC-BOTTLE", "塑料瓶", "TST-09-PLASTIC", 30),
    ("TST-09-PAPER", "纸质包装", "09", 30),
    ("TST-09-PAPER-CARTON", "纸箱", "TST-09-PAPER", 10),
    ("TST-09-PAPER-GIFT", "礼盒", "TST-09-PAPER", 20),
    ("TST-09-PAPER-BAG", "纸袋", "TST-09-PAPER", 30),
    ("TST-99", "其他商品", None, 990),
    ("TST-99-PENDING", "待分类", "TST-99", 10),
    ("TST-99-PENDING-GENERAL", "综合商品", "TST-99-PENDING", 10),
    ("TST-99-PENDING-TEST", "测试商品", "TST-99-PENDING", 20),
)


# Specific terms come first. Generic packaging words deliberately exclude
# “礼盒”, avoiding the common mistake of classifying gift-packaged food as
# packaging material.
CLASSIFICATION_RULES = (
    ("TST-09-FOOD-SEAL", ("封口膜", "铝膜")),
    ("TST-09-FOOD-CAN", ("包装罐", "罐子", "罐盖")),
    ("TST-09-FOOD-LID", ("瓶盖", "盖子")),
    ("TST-09-PLASTIC-BAG", ("包装袋", "真空袋", "塑料袋")),
    ("TST-09-PAPER-CARTON", ("纸箱", "包装箱")),
    ("TST-08-LIFE-WET", ("湿巾",)),
    ("TST-08-LIFE-TISSUE", ("抽纸", "面巾纸", "纸巾")),
    ("TST-08-LIFE-ROLL", ("卷纸", "卫生纸")),
    ("TST-08-OFFICE-PRINT", ("打印纸", "复印纸")),
    ("TST-08-OFFICE-LABEL", ("标签纸", "不干胶")),
    ("TST-07-HOME-LAUNDRY", ("洗衣液", "洗衣粉", "柔顺剂", "洗衣凝珠")),
    ("TST-07-HOME-KITCHEN", ("洗洁精", "洁厕", "油污净", "厨卫净")),
    ("TST-07-HOME-ROOM", ("消毒液", "空气清新", "地板清洁")),
    ("TST-07-PERSONAL-HAIR", ("洗发", "护发")),
    ("TST-07-PERSONAL-BATH", ("沐浴露", "香皂", "沐浴乳")),
    ("TST-07-PERSONAL-ORAL", ("牙膏", "牙刷", "漱口水")),
    ("TST-07-BEAUTY-FACE", ("面膜", "洁面", "面霜", "精华液")),
    ("TST-07-BEAUTY-BODY", ("身体乳", "护手霜", "润肤")),
    ("TST-06-EGG-CHICKEN", ("鸡蛋",)),
    ("TST-06-EGG-OTHER", ("鸭蛋", "鹌鹑蛋", "皮蛋", "咸蛋")),
    ("TST-06-MEAT-CURED", ("腊肉", "腊肠", "香肠", "熟食")),
    ("TST-06-MEAT-POULTRY", ("鸡肉", "鸡翅", "鸡腿", "鸭肉", "鹅肉")),
    ("TST-06-MEAT-LIVESTOCK", ("猪肉", "牛肉", "羊肉", "排骨")),
    ("TST-06-SEAFOOD-SHELL", ("虾", "蟹", "贝")),
    ("TST-06-SEAFOOD-FISH", ("鱼",)),
    ("TST-04-NUT-NUT", ("巴旦木", "松子", "开心果", "夏威夷果", "核桃", "腰果")),
    ("TST-04-DRIED-RAISIN", ("葡萄干",)),
    ("TST-04-DRIED-FRUIT", ("芒果干", "水果干", "果干", "板栗仁")),
    ("TST-04-DRY-MUSHROOM", ("干香菇", "木耳", "银耳", "干菌")),
    ("TST-06-PRODUCE-MUSHROOM", ("鲜菇", "蘑菇", "香菇")),
    ("TST-06-PRODUCE-VEGETABLE", ("蔬菜", "青菜", "白菜", "番茄", "土豆")),
    ("TST-06-PRODUCE-FRUIT", ("水果", "苹果", "香蕉", "橙", "葡萄")),
    ("TST-04-SNACK-BAKED", ("饼干", "蛋糕", "糕点", "面包")),
    ("TST-04-SNACK-CANDY", ("糖果", "巧克力")),
    ("TST-04-SNACK-PUFFED", ("薯片", "虾条", "膨化")),
    ("TST-03-OIL-PEANUT", ("花生油",)),
    ("TST-03-OIL-BLEND", ("调和油",)),
    ("TST-03-OIL-OTHER", ("食用油", "菜籽油", "玉米油", "大豆油")),
    ("TST-03-SEASONING-SAUCE", ("酱油", "陈醋", "米醋")),
    ("TST-03-SEASONING-SALT", ("食盐", "白糖", "冰糖")),
    ("TST-03-SEASONING-SPICE", ("调味料", "辣椒", "香辛料", "豆瓣酱")),
    ("TST-03-NOODLE-FLOUR", ("面粉",)),
    ("TST-03-NOODLE-DRY", ("挂面", "面条")),
    ("TST-03-NOODLE-INSTANT", ("方便面", "米粉", "粉丝")),
    ("TST-03-RICE-OTHER", ("大米", "香米", "米")),
    ("TST-02-DAIRY-SOY", ("豆奶", "豆浆")),
    ("TST-02-DAIRY-MILK", ("牛奶", "酸奶", "乳饮")),
    ("TST-02-WATER-PLAIN", ("矿泉水", "纯净水", "饮用水")),
    ("TST-02-WATER-SODA", ("可乐", "汽水", "苏打水")),
    ("TST-02-WATER-JUICE", ("果汁", "茶饮")),
    ("TST-02-INSTANT-COFFEE", ("咖啡",)),
    ("TST-02-INSTANT-TEA", ("茶叶",)),
    ("TST-02-INSTANT-POWDER", ("固体饮料", "冲调粉")),
    ("TST-01-BEER-CN", ("啤酒",)),
    ("TST-01-WINE-RED", ("红葡萄酒", "红酒")),
    ("TST-01-WINE-WHITE", ("白葡萄酒",)),
    ("TST-01-BAIJIU-JIANG", ("酱香",)),
    ("TST-01-BAIJIU-NONG", ("浓香",)),
    ("TST-01-BAIJIU-OTHER", ("白酒",)),
    ("TST-05-CATERING-TABLEWARE", ("一次性餐具", "筷子", "餐盒")),
    ("TST-05-CATERING-CUP", ("纸杯", "吸管", "一次性碗")),
    ("TST-05-CATERING-FRESH", ("保鲜膜", "保鲜袋")),
    ("TST-05-CLEAN-BAG", ("垃圾袋",)),
    ("TST-05-CLEAN-TOOL", ("拖把", "扫把", "抹布", "百洁布")),
    ("TST-05-CLEAN-LABOR", ("手套", "口罩", "劳保")),
)

FALLBACK_CATEGORY_CODE = "TST-99-PENDING-GENERAL"


def _normalized_name(name):
    return re.sub(r"\s+", "", str(name or "")).lower()


def classify_product_name(name):
    normalized = _normalized_name(name)
    for category_code, keywords in CLASSIFICATION_RULES:
        if any(_normalized_name(keyword) in normalized for keyword in keywords):
            return category_code, True
    return FALLBACK_CATEGORY_CODE, False


class Command(BaseCommand):
    help = "预览或补充超市商城三级测试分类，并宽松归类现有未分类商品"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="实际写入 wms_db；不提供该参数时只输出预览",
        )

    def handle(self, *args, **options):
        database_name = self._validate_database()
        missing_category_count = self._validate_category_plan()
        preview = self._build_assignment_plan(lock=False)
        self._write_preview(database_name, missing_category_count, preview)

        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING("预览完成，数据库未修改；确认后请增加 --apply。")
            )
            return

        with transaction.atomic():
            categories, created_count = self._ensure_categories()
            locked_plan = self._build_assignment_plan(lock=True)
            assigned_count = self._assign_products(
                locked_plan["assignments"], categories
            )
            if assigned_count != locked_plan["uncategorized_count"]:
                raise CommandError(
                    "分类更新数量不守恒："
                    f"预计 {locked_plan['uncategorized_count']}，实际 {assigned_count}"
                )
            remaining = Product.objects.filter(category__isnull=True).count()
            if remaining:
                raise CommandError(f"执行后仍有 {remaining} 个商品未分类，事务已回滚")

        self.stdout.write(
            self.style.SUCCESS(
                "执行完成："
                f"新增分类 {created_count} 个，归类商品 {assigned_count} 个，"
                f"保留已有分类商品 {preview['classified_count']} 个。"
            )
        )

    def _validate_database(self):
        database_name = str(connection.settings_dict.get("NAME") or "")
        if database_name != "wms_db":
            raise CommandError(
                f"安全检查失败：当前数据库为 {database_name or '<空>'}，"
                "该命令只允许写入 wms_db。"
            )
        return database_name

    def _validate_category_plan(self):
        existing_by_code = {
            row.code.upper(): row
            for row in ProductCategory.all_objects.select_related("parent").all()
        }
        planned_codes = set()
        planned_siblings = set()
        missing_count = 0

        for code, name, parent_code, _sort_order in CATEGORY_SPECS:
            if code in planned_codes:
                raise CommandError(f"测试分类配置存在重复编码：{code}")
            if parent_code and parent_code not in planned_codes:
                raise CommandError(f"测试分类配置父级顺序错误：{code} -> {parent_code}")
            sibling_key = (parent_code, name.casefold())
            if sibling_key in planned_siblings:
                raise CommandError(f"测试分类配置存在同级重名：{name}")
            planned_codes.add(code)
            planned_siblings.add(sibling_key)

            existing = existing_by_code.get(code)
            if existing is None:
                missing_count += 1
                continue
            actual_parent_code = existing.parent.code if existing.parent_id else None
            if existing.name != name or actual_parent_code != parent_code:
                raise CommandError(
                    f"分类编码 {code} 已被其他分类占用："
                    f"当前 {actual_parent_code or '-'} / {existing.name}，"
                    f"计划 {parent_code or '-'} / {name}"
                )
            if existing.is_deleted or not existing.is_active:
                raise CommandError(f"计划使用的分类 {code} 已停用或删除，请先人工处理")

        return missing_count

    def _build_assignment_plan(self, *, lock):
        queryset = Product.objects.order_by("id")
        if lock:
            queryset = queryset.select_for_update()
        total_count = queryset.count()
        classified_count = queryset.exclude(category__isnull=True).count()
        rows = list(queryset.filter(category__isnull=True).values_list("id", "name"))
        assignments = defaultdict(list)
        matched_count = 0
        for product_id, name in rows:
            category_code, matched = classify_product_name(name)
            assignments[category_code].append(product_id)
            matched_count += int(matched)
        return {
            "total_count": total_count,
            "classified_count": classified_count,
            "uncategorized_count": len(rows),
            "matched_count": matched_count,
            "fallback_count": len(rows) - matched_count,
            "assignments": dict(assignments),
        }

    def _write_preview(self, database_name, missing_category_count, plan):
        self.stdout.write(f"数据库：{database_name}")
        self.stdout.write(
            "商品："
            f"总数 {plan['total_count']}，已有分类 {plan['classified_count']}，"
            f"待归类 {plan['uncategorized_count']}"
        )
        self.stdout.write(
            f"分类：计划总数 {len(CATEGORY_SPECS)}，待新增 {missing_category_count}"
        )
        self.stdout.write(
            "匹配："
            f"关键词命中 {plan['matched_count']}，"
            f"进入综合商品 {plan['fallback_count']}"
        )
        counts = Counter(
            {
                category_code: len(product_ids)
                for category_code, product_ids in plan["assignments"].items()
            }
        )
        for category_code, count in sorted(
            counts.items(), key=lambda item: (-item[1], item[0])
        ):
            self.stdout.write(f"  {self._planned_path(category_code)}：{count}")

    def _planned_path(self, category_code):
        specs = {
            code: (name, parent_code)
            for code, name, parent_code, _sort_order in CATEGORY_SPECS
        }
        parts = []
        seen = set()
        code = category_code
        while code and code not in seen:
            seen.add(code)
            name, code = specs[code]
            parts.append(name)
        return " > ".join(reversed(parts))

    def _ensure_categories(self):
        categories = {}
        created_count = 0
        for code, name, parent_code, sort_order in CATEGORY_SPECS:
            parent = categories.get(parent_code)
            category = ProductCategory.all_objects.filter(code=code).first()
            if category is None:
                category = ProductCategory.objects.create(
                    code=code,
                    name=name,
                    parent=parent,
                    sort_order=sort_order,
                    is_active=True,
                    remark="商城三级分类测试数据",
                )
                created_count += 1
            categories[code] = category
        return categories, created_count

    def _assign_products(self, assignments, categories):
        assigned_count = 0
        for category_code, product_ids in assignments.items():
            category = categories[category_code]
            updated = Product.objects.filter(
                id__in=product_ids,
                category__isnull=True,
            ).update(category_id=category.id)
            if updated != len(product_ids):
                raise CommandError(
                    f"{category.full_path} 更新数量不一致："
                    f"预计 {len(product_ids)}，实际 {updated}"
                )
            assigned_count += updated
        return assigned_count
