"""演示数据生成。

为什么需要它：README 承诺"克隆下来就有约 1200 行演示数据"，但之前的
`sales_insight.db` 是手工灌的 —— 换个环境 clone 下来就是空库，
看板全空、Agent 测试也因为没有维表数据而失败。

设计要点：
1. **确定性**：固定随机种子。同样的代码永远生成同样的数据，
   测试断言和截图才不会今天一个样明天一个样。
2. **幂等**：`fact_sales` 已有数据就直接返回，重复启动不会翻倍。
3. **不依赖外部文件**：数据在代码里生成，clone 即用，不引入 CSV 资产。

数据形态刻意贴近真实业务：区域名是「华东」而不是「华东区」
（这正是 Text2SQL 那个坑的由来），月份覆盖最近四个月，
所以"上个月营收"这类相对时间查询能查到东西。
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from sqlalchemy import func, select

from app.core.logging import get_logger
from app.db.models.sales import DimCustomer, DimProduct, DimRegion, FactSales
from app.db.session import SessionLocal

log = get_logger(__name__)

#: 固定种子 —— 保证不同机器、不同次运行生成的数据完全一致
SEED = 20261020

#: 区域名刻意不带"区"字，模拟真实库里"华东区 vs 华东"的取值不一致问题
REGIONS = [
    ("east", "华东"),
    ("north", "华北"),
    ("south", "华南"),
    ("central", "华中"),
    ("southwest", "西南"),
]

CUSTOMERS = [
    ("C001", "恒瑞医疗器械", "医疗", "KA"),
    ("C002", "联众供应链", "贸易", "KA"),
    ("C003", "海通物流", "物流", "大客户"),
    ("C004", "星辰电子", "制造", "大客户"),
    ("C005", "百汇商贸", "贸易", "中小"),
    ("C006", "瑞康药业", "医疗", "KA"),
    ("C007", "东升建材", "建筑", "大客户"),
    ("C008", "万和食品", "快消", "中小"),
    ("C009", "云图科技", "科技", "大客户"),
    ("C010", "金桥零售", "零售", "中小"),
    ("C011", "博远机械", "制造", "中小"),
    ("C012", "天成能源", "能源", "KA"),
]

PRODUCTS = [
    ("SKU-1001", "监护仪 X200", "医疗设备", "瑞泰", "台", 48000),
    ("SKU-1002", "输液泵 P50", "医疗设备", "瑞泰", "台", 12600),
    ("SKU-2001", "工业交换机 S24", "网络设备", "华讯", "台", 3200),
    ("SKU-2002", "光模块 10G", "网络设备", "华讯", "只", 460),
    ("SKU-3001", "伺服电机 M3", "工业控制", "德川", "台", 8900),
    ("SKU-3002", "变频器 V7", "工业控制", "德川", "台", 5400),
    ("SKU-4001", "条码扫描枪 B2", "外设", "码捷", "把", 780),
    ("SKU-4002", "标签打印机 T8", "外设", "码捷", "台", 2300),
    ("SKU-5001", "冷链温度记录仪", "物联网", "冰鉴", "个", 1150),
    ("SKU-5002", "温湿度传感器 H1", "物联网", "冰鉴", "个", 320),
]

CHANNELS = ["直销", "经销", "电商", "老客户复购"]
SALES_REPS = ["张伟", "李静", "王强", "赵敏", "陈晨", "刘洋"]

#: 生成多少行事实数据
ROWS = 1200

#: 覆盖最近四个月，保证"上个月""本月"这类相对时间都有数据
END_DATE = date(2026, 10, 19)
START_DATE = date(2026, 7, 1)


def _clear(session) -> None:  # noqa: ANN001
    """先删事实表再删维表，避免外键约束报错。"""
    session.query(FactSales).delete()
    session.query(DimCustomer).delete()
    session.query(DimProduct).delete()
    session.query(DimRegion).delete()
    session.commit()


def ensure_demo_data(*, force: bool = False) -> int:
    """幂等地灌入演示数据，返回写入的事实表行数（已存在则返回 0）。"""
    with SessionLocal() as session:
        existing = session.execute(select(func.count(FactSales.id))).scalar() or 0
        if existing > 0 and not force:
            return 0

        if force:
            _clear(session)

        # ---- 维表 ----
        region_ids: dict[str, int] = {}
        for code, name in REGIONS:
            r = DimRegion(code=code, name=name)
            session.add(r)
            session.flush()  # 拿到自增 id 供事实表引用
            region_ids[code] = r.id

        customer_ids: dict[str, int] = {}
        for code, name, industry, level in CUSTOMERS:
            c = DimCustomer(
                code=code,
                name=name,
                industry=industry,
                level=level,
                owner=random.Random(SEED).choice(SALES_REPS),
            )
            session.add(c)
            session.flush()
            customer_ids[code] = c.id

        product_ids: dict[str, int] = {}
        base_price: dict[str, float] = {}
        for sku, name, category, brand, unit, price in PRODUCTS:
            p = DimProduct(sku=sku, name=name, category=category, brand=brand, unit=unit)
            session.add(p)
            session.flush()
            product_ids[sku] = p.id
            base_price[sku] = float(price)

        # ---- 事实表 ----
        rnd = random.Random(SEED)  # 固定种子：每次生成完全一致
        span = (END_DATE - START_DATE).days
        rows: list[FactSales] = []

        for i in range(ROWS):
            d = START_DATE + timedelta(days=rnd.randint(0, span))
            region_code, _ = rnd.choice(REGIONS)
            cust_code = rnd.choice(CUSTOMERS)[0]
            sku = rnd.choice(PRODUCTS)[0]

            # 按"订单预算"反推数量：贵的设备买 1~3 台，便宜的耗材买几十上百个。
            # 若直接给 1~35 的随机数量，会出现"一次买 35 台监护仪＝168 万"这种
            # 明显不真实的单据，看板上的客单价也就失真了。
            budget = rnd.uniform(3000, 55000)
            qty = max(1, min(int(budget / base_price[sku]), 200))
            # 单价围绕基准价上下浮动，偶尔有折扣
            price = base_price[sku] * rnd.uniform(0.88, 1.06)
            revenue = round(qty * price, 2)
            # 毛利率控制在 25%~45% 区间，避免出现负毛利的怪数据
            cost = round(revenue * rnd.uniform(0.55, 0.75), 2)

            rows.append(
                FactSales(
                    date=d,
                    region_id=region_ids[region_code],
                    customer_id=customer_ids[cust_code],
                    product_id=product_ids[sku],
                    region_code=region_code,
                    customer_code=cust_code,
                    sku=sku,
                    channel=rnd.choice(CHANNELS),
                    sales_rep=rnd.choice(SALES_REPS),
                    order_no=f"SO{d:%Y%m%d}{i:04d}",
                    quantity=qty,
                    unit_price=round(price, 4),
                    revenue=revenue,
                    cost=cost,
                    gross_profit=round(revenue - cost, 2),
                )
            )

        session.add_all(rows)
        session.commit()

        log.info(
            "demo_data_created rows=%d regions=%d customers=%d products=%d",
            len(rows),
            len(REGIONS),
            len(CUSTOMERS),
            len(PRODUCTS),
        )
        return len(rows)
