import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Float, Integer, Boolean, DateTime, Text, Enum as SAEnum,
    ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import relationship
from database import Base
import enum


def generate_uuid():
    return str(uuid.uuid4())


DEFAULT_STORE_ID = "default-store"


class Store(Base):
    """代运营店铺。阶段一先建立默认店铺，后续接入各店独立同步。"""
    __tablename__ = "stores"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)
    platform = Column(String, default="Takealot")
    status = Column(String, default="active")
    owner_name = Column(String, default="")
    sync_method = Column(String, default="manual")
    sync_interval_minutes = Column(Integer, default=60)
    external_store_ref = Column(String, default="")
    allow_negative_inventory_shipments = Column(Boolean, default=False)
    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SelectionStatus(str, enum.Enum):
    DATA_INCOMPLETE = "数据待补充"
    CATEGORY_PENDING = "待确认品类"
    QUALIFIED = "合格选品"
    NOT_RECOMMENDED = "不建议选品"


class Product(Base):
    __tablename__ = "products"

    id = Column(String, primary_key=True, default=generate_uuid)
    store_id = Column(String, ForeignKey("stores.id"), nullable=False, default=DEFAULT_STORE_ID, index=True)
    product_no = Column(Integer, autoincrement=True)
    is_archived = Column(Boolean, default=False, nullable=False, index=True)
    archived_at = Column(DateTime, nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow)
    note = Column(Text, default="")

    # Takealot fields
    takealot_url = Column(String, default="")
    # 商品身份锚点。原来只能从 takealot_url 里正则抠 PLID，链接一空（手动新建、
    # 抓取失败、卖家后台导入）身份就丢了，同一件商品会被重复导入。存在这一列之后
    # 身份不再依赖链接的 slug 写法，链接只用于展示和点击。
    plid = Column(String, default="", index=True)
    tsin = Column(String, default="")
    product_name = Column(String, default="")
    product_image_url = Column(String, default="")
    product_image_path = Column(String, default="")
    actual_sale_price_zar = Column(Float, nullable=True)

    # Fee fields
    fee_category = Column(String, nullable=True)
    fee_category_confirmed = Column(Boolean, default=False)
    fee_rate_range = Column(String, default="")
    success_fee_rate = Column(Float, nullable=True)
    success_fee_cap = Column(Float, nullable=True)

    # Purchase fields
    purchase_url = Column(String, default="")
    sku = Column(String, default="")
    chinese_product_name = Column(String, default="")
    purchase_cost_cny = Column(Float, nullable=True)
    purchase_shipping_cny = Column(Float, nullable=True)
    purchase_quantity = Column(Integer, default=4)
    unit_price_cny = Column(Float, nullable=True)

    # Dimensions & weight
    length_mm = Column(Float, nullable=True)
    width_mm = Column(Float, nullable=True)
    height_mm = Column(Float, nullable=True)
    actual_weight_kg = Column(Float, nullable=True)

    # Domestic costs
    packaging_cost_per_unit_cny = Column(Float, default=1.0)

    # Shipping
    shipping_method = Column(String, nullable=True)

    # Overseas warehouse costs
    inbound_listing_fee_cny = Column(Float, default=0.75)
    outbound_operation_fee_cny = Column(Float, default=0.70)
    last_mile_delivery_fee_cny = Column(Float, default=2.00)
    other_fee_cny = Column(Float, default=2.00)

    # Platform fees
    fulfillment_fee_zar = Column(Float, default=42.00)

    # Manual cost overrides - 为None时使用公式计算值，不为None时使用此手动值
    manual_domestic_forwarding_cny = Column(Float, nullable=True)   # 手动前置仓快递费(CNY)
    manual_international_shipping_cny = Column(Float, nullable=True) # 手动国际头程(CNY)
    manual_overseas_op_cost_cny = Column(Float, nullable=True)      # 手动海外仓操作费(CNY)
    manual_success_fee_zar = Column(Float, nullable=True)           # 手动Success Fee(ZAR)
    manual_fulfillment_fee_zar = Column(Float, nullable=True)       # 手动Fulfillment Fee(ZAR)
    manual_total_cost_zar = Column(Float, nullable=True)            # 手动总成本(ZAR)

    # Market signals from scraping
    competing_sellers_count = Column(Integer, nullable=True)
    stock_remaining = Column(Integer, nullable=True)
    review_count = Column(Integer, nullable=True)
    rating_value = Column(Float, nullable=True)

    # 多市场参考价（JSON 文本，空串表示未采集）：
    # {"CN":{"price":469,"cur":"CNY","src":"官网","at":"2026-10-21"},
    #  "SG":{"price":115,"cur":"SGD","src":"官网","at":"2026-10-21","url":"..."},
    #  "TB":{"price":268,"cur":"CNY","src":"淘宝均价","n":12,"at":"2026-10-21"}}
    # 只做展示与比价参考，不参与利润计算，因此不拆字段/不建表。
    market_prices = Column(Text, default="")

    # Calculated results (computed fields, stored for query efficiency)
    volume_cbm = Column(Float, nullable=True)
    volumetric_weight_kg = Column(Float, nullable=True)
    chargeable_weight_kg = Column(Float, nullable=True)
    domestic_forwarding_cost_per_unit_cny = Column(Float, nullable=True)
    unit_product_cost_cny = Column(Float, nullable=True)
    international_shipping_per_unit_cny = Column(Float, nullable=True)
    cost_to_sa_warehouse_cny = Column(Float, nullable=True)
    cost_to_sa_warehouse_zar = Column(Float, nullable=True)
    overseas_warehouse_operation_cost_cny = Column(Float, nullable=True)
    overseas_warehouse_operation_cost_zar = Column(Float, nullable=True)
    success_fee_zar = Column(Float, nullable=True)
    official_total_cost_zar = Column(Float, nullable=True)
    total_cost_zar = Column(Float, nullable=True)
    profit_zar = Column(Float, nullable=True)
    profit_margin = Column(Float, nullable=True)
    minimum_price_at_20_margin = Column(Float, nullable=True)
    minimum_price_at_15_margin = Column(Float, nullable=True)

    # Link management
    link_status = Column(String, default="未购买")  # 未购买 / 已购买 / 已上架

    # Management
    selection_status = Column(String, default=SelectionStatus.DATA_INCOMPLETE.value)
    exchange_rate_used = Column(Float, default=0.41)
    fee_rate_used = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    scrape_logs = relationship("ScrapeLog", back_populates="product", cascade="all, delete-orphan")


class FeeCategory(Base):
    __tablename__ = "fee_categories"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, unique=True, nullable=False)
    fee_rate_range = Column(String, default="")
    success_fee_rate = Column(Float, nullable=False)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FeeMappingRule(Base):
    __tablename__ = "fee_mapping_rules"

    id = Column(String, primary_key=True, default=generate_uuid)
    takealot_category_pattern = Column(String, default="")
    title_keyword_pattern = Column(String, default="")
    fee_category = Column(String, nullable=False)
    priority = Column(Integer, default=0)
    active = Column(Boolean, default=True)
    created_by_user = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ScrapeLog(Base):
    __tablename__ = "scrape_logs"

    id = Column(String, primary_key=True, default=generate_uuid)
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    original_url = Column(String, default="")
    scrape_time = Column(DateTime, default=datetime.utcnow)
    original_title = Column(String, default="")
    original_price = Column(String, default="")
    result = Column(String, default="")
    error_message = Column(Text, default="")

    product = relationship("Product", back_populates="scrape_logs")


class SystemSettings(Base):
    __tablename__ = "system_settings"

    id = Column(String, primary_key=True, default=generate_uuid)
    key = Column(String, unique=True, nullable=False)
    value = Column(String, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProcurementRecord(Base):
    """采购记录：仅做记录，不联动产品数据（对齐本地 Rust procurement_records 表）"""
    __tablename__ = "procurement_records"

    id = Column(String, primary_key=True, default=generate_uuid)
    store_id = Column(String, ForeignKey("stores.id"), nullable=False, default=DEFAULT_STORE_ID, index=True)
    product_id = Column(String, ForeignKey("products.id"), nullable=True)
    product_no = Column(Integer, nullable=True)
    product_name = Column(String, default="")
    quantity = Column(Integer, default=1)
    total_amount = Column(Float, default=0.0)
    unit_price = Column(Float, default=0.0)
    notes = Column(String, default="")
    status = Column(String, default="received", nullable=False)  # in_transit / received / cancelled
    status_updated_at = Column(DateTime, default=datetime.utcnow)
    recorded_at = Column(String, default="")


class OperationLog(Base):
    """店铺级关键操作日志，用于追踪库存和业务状态变化。"""
    __tablename__ = "operation_logs"

    id = Column(String, primary_key=True, default=generate_uuid)
    store_id = Column(String, ForeignKey("stores.id"), nullable=False, default=DEFAULT_STORE_ID, index=True)
    entity_type = Column(String, nullable=False, index=True)
    entity_id = Column(String, default="")
    action = Column(String, nullable=False)
    summary = Column(String, default="")
    details = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class PackingProduct(Base):
    """云端装箱单产品（对齐本地 kunjia_analyze products 表 14 字段 + updated_at）

    字段顺序与本地 sqlite products 表一致：
    sku / name / name_zh / name_en / unit / weight / material / brand /
    battery / electric / magnetic / template_row / default_count / image_file

    多店铺隔离：主键是 (store_id, sku)。同一个 SKU 在两个店铺是两条互不影响的
    记录，重量/材质/图片/模板行不会互相覆盖。
    """
    __tablename__ = "packing_products"

    store_id = Column(String, ForeignKey("stores.id"), nullable=False, default=DEFAULT_STORE_ID, primary_key=True, index=True)
    sku = Column(String, primary_key=True)
    name = Column(String, nullable=False, default="")
    name_zh = Column(String, default="")
    name_en = Column(String, default="")
    unit = Column(String, default="")
    weight = Column(String, default="")
    material = Column(String, default="")
    brand = Column(String, default="")
    battery = Column(String, default="")
    electric = Column(String, default="")
    magnetic = Column(String, default="")
    template_row = Column(Integer, nullable=True)
    default_count = Column(Integer, default=1)
    image_file = Column(String, default="")
    variant_group = Column(String, default="")
    variant_label = Column(String, default="")
    is_primary_variant = Column(Boolean, default=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class InventoryAdjustment(Base):
    """人工库存调整流水；记录只追加、不覆盖，保证库存变化可追溯。"""
    __tablename__ = "inventory_adjustments"

    id = Column(String, primary_key=True, default=generate_uuid)
    store_id = Column(String, ForeignKey("stores.id"), nullable=False, default=DEFAULT_STORE_ID, index=True)
    sku = Column(String, nullable=False, index=True)
    quantity_delta = Column(Integer, nullable=False)
    reason = Column(String, default="")
    notes = Column(String, default="")
    occurred_at = Column(String, default="")
    reversed_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Shipment(Base):
    """装箱/发货快照。只有 confirmed 状态扣库存；exported 仅留档。

    发货单号按店铺独立编号，因此唯一约束是 (store_id, shipment_no)，
    两个店铺同一天都可以有 PK-YYYYMMDD-001。
    """
    __tablename__ = "shipments"
    __table_args__ = (UniqueConstraint("store_id", "shipment_no", name="ux_shipments_store_no"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    store_id = Column(String, ForeignKey("stores.id"), nullable=False, default=DEFAULT_STORE_ID, index=True)
    draft_key = Column(String, unique=True, nullable=False, index=True)
    shipment_no = Column(String, nullable=False, index=True)
    shipment_date = Column(String, nullable=False)
    mark = Column(String, default="")
    shipping = Column(String, default="")
    address = Column(String, default="")
    status = Column(String, default="confirmed")
    version = Column(Integer, default=1)
    void_reason = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    voided_at = Column(DateTime, nullable=True)


class ShipmentLine(Base):
    __tablename__ = "shipment_lines"

    id = Column(String, primary_key=True, default=generate_uuid)
    shipment_id = Column(String, ForeignKey("shipments.id"), nullable=False, index=True)
    sku = Column(String, nullable=False, index=True)
    name_zh = Column(String, default="")
    name_en = Column(String, default="")
    image_file = Column(String, default="")
    cartons = Column(Integer, default=1)
    count_per_carton = Column(Integer, default=1)
    total_quantity = Column(Integer, default=1)


class ShipmentAllocation(Base):
    """已发往海外的逐行来源；海外在途不计入国内可用库存。"""
    __tablename__ = "shipment_allocations"
    line_id = Column(String, ForeignKey("shipment_lines.id"), primary_key=True)
    domestic_quantity = Column(Integer, nullable=False)
    legacy_quantity = Column(Integer, nullable=False)


class ShipmentCarton(Base):
    """逐箱清单：一箱一条，记「第几箱里装了什么」。

    `ShipmentLine` 是**按 SKU 汇总**（A 有 3 箱 × 10 件），回答不了「第 3 箱装的是
    什么」——箱子封上以后外观一样，这正是装完就忘的根因。所以再加一层按箱的记录：

    - `seq` 是这一箱在整票货里的序号（1 起），箱唛上印「第 3 箱 / 共 5 箱」；
    - `box_no` 是印在箱子外面的编号（默认 C01、C02…），可以手改成自己的规则；
    - 一箱一个 SKU 时导出会自动展开成逐箱；混装由人工在装箱页录入。

    和 Shipment 一样按 store 隔离：同一票货不会跨店铺。
    """
    __tablename__ = "shipment_cartons"
    __table_args__ = (UniqueConstraint("shipment_id", "seq", name="ux_cartons_shipment_seq"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    shipment_id = Column(String, ForeignKey("shipments.id"), nullable=False, index=True)
    store_id = Column(String, ForeignKey("stores.id"), nullable=False, default=DEFAULT_STORE_ID, index=True)
    seq = Column(Integer, nullable=False)
    box_no = Column(String, default="")
    note = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class ShipmentCartonItem(Base):
    """箱内明细：一个箱子里的一行 SKU（混装时一箱多行）。"""
    __tablename__ = "shipment_carton_items"

    id = Column(String, primary_key=True, default=generate_uuid)
    carton_id = Column(String, ForeignKey("shipment_cartons.id"), nullable=False, index=True)
    sku = Column(String, nullable=False, index=True)
    name_zh = Column(String, default="")
    name_en = Column(String, default="")
    quantity = Column(Integer, nullable=False, default=1)


class SkuRename(Base):
    """SKU 变更留档：以卖家后台为准更正 SKU 时，旧值留在这里可追溯。

    这张表只追加、不修改。`detail` 记录本次一并迁移了哪些表、各改了几行，
    回答的是「库存/发运里那个旧 SKU 去哪了」——不留档的话，改完之后
    历史单据里的 SKU 就再也无法对应回任何一个商品。
    """
    __tablename__ = "sku_renames"

    id = Column(String, primary_key=True, default=generate_uuid)
    store_id = Column(String, ForeignKey("stores.id"), nullable=False, default=DEFAULT_STORE_ID, index=True)
    product_id = Column(String, nullable=False, index=True)
    old_sku = Column(String, nullable=False, index=True)
    new_sku = Column(String, nullable=False, index=True)
    source = Column(String, default="seller_api")
    detail = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ExternalOffer(Base):
    """站外货源：1688 / Takealot 上看到的、**不属于自己仓库**的商品。

    存在的理由：详情页原来只推荐库内商品，而库内只有自己上架过的东西——
    看到一个品类时，「同类还有谁在卖、进货价大概多少」这类信息库里没有。
    这张表存的就是站外看到的东西，按 `keywords` 挂到商品下面。

    `price` + `currency` 必须成对存：1688 是人民币批发价、Takealot 是兰特零售价，
    混成一个数字会在界面上被当成同一种钱直接比大小。
    """
    __tablename__ = "external_offers"

    id = Column(String, primary_key=True, default=generate_uuid)
    store_id = Column(String, ForeignKey("stores.id"), nullable=False, default=DEFAULT_STORE_ID, index=True)
    platform = Column(String, nullable=False, default="1688", index=True)   # 1688 / takealot
    url = Column(String, nullable=False)
    title = Column(String, default="")
    title_cn = Column(String, default="")
    price = Column(Float)                # 站外标价，未换算
    currency = Column(String, default="CNY")   # CNY / ZAR
    moq = Column(Integer)                # 起订量，1688 才有
    image_url = Column(String, default="")
    keywords = Column(Text, default="")  # 逗号分隔，命中商品标题就挂到该商品下
    usage = Column(String, default="")   # 用途
    scenario = Column(String, default="")  # 适用场景
    reason = Column(String, default="")  # 选择理由
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SelectionItem(Base):
    """选品池：待核算的候选商品，**不是**产品主档。

    为什么单独建表而不写进 `products`
    ---------------------------------
    `products` 是库存、打包、采购三块的锚点（`inventory_routes` 查库存时只按
    `store_id + is_archived` 过滤，不看 `selection_status`；打包有 32 处引用、
    采购 10 处）。把一批待核算的候选写进去，它们会立刻出现在库存和打包清单里，
    被当成真货处理；而一轮选品 1800 条里绝大多数会被淘汰，删掉又会在
    `product_no` 上留下空洞。

    所以草稿留在本表，只有勾选并「一键新建产品」时才复制进 `products`，
    并把 `promoted_product_id` 指回去——之后 `products` 里那条是真数据，
    本表只作为来源记录。

    三类输入都能落进来
    ------------------
    - 只有链接（竞品店铺导出）→ `takealot_url`，PLID 从链接里取
    - 只有 TSIN（Takealot 销售报表）→ `tsin`，enrich 时用卖家 API 反查 PLID
    - 链接 + 销售数据（自己整理的选品表）→ 全字段都有

    `status` 与 `Product.selection_status` 用词一致，但含义更粗：
    待核算 / 合格 / 不合格 / 已建产品。
    """

    __tablename__ = "selection_items"

    id = Column(String, primary_key=True, default=generate_uuid)
    store_id = Column(String, ForeignKey("stores.id"), nullable=False, default=DEFAULT_STORE_ID, index=True)

    # 一批 = 一次上传。batch_id 用时间戳+随机串，便于整批清掉重来。
    batch_id = Column(String, default="", index=True)
    batch_name = Column(String, default="")
    source_row = Column(Integer, nullable=True)

    # ---- 身份 ----
    plid = Column(String, default="", index=True)
    tsin = Column(String, default="", index=True)
    takealot_url = Column(String, default="")
    product_name = Column(String, default="")
    chinese_product_name = Column(String, default="")
    brand = Column(String, default="")
    product_image_url = Column(String, default="")
    image_urls = Column(Text, default="")      # JSON 数组，识图时一次给 3 张

    # ---- 类目与费率 ----
    takealot_category_path = Column(String, default="")
    fee_category = Column(String, default="")
    fee_category_confidence = Column(String, default="")   # high / medium / low
    fee_category_reason = Column(String, default="")

    # ---- 市场信号（公开接口或原表带来）----
    actual_sale_price_zar = Column(Float, nullable=True)
    rating_value = Column(Float, nullable=True)
    review_count = Column(Integer, nullable=True)

    # ---- 原表里的销售数据（可能为空：竞品表就没有）----
    sales_qty = Column(Integer, nullable=True)
    net_sales_zar = Column(Float, nullable=True)
    gross_sales_zar = Column(Float, nullable=True)
    net_sales_cny = Column(Float, nullable=True)
    tier = Column(String, default="")          # S / A / B / C，无销售数据时留空

    # ---- 与产品列表的关系 ----
    already_in_products = Column(Boolean, default=False, index=True)
    promoted_product_id = Column(String, nullable=True)

    # ---- 重尺（手填为主，公开接口拿到就预填）----
    length_mm = Column(Float, nullable=True)
    width_mm = Column(Float, nullable=True)
    height_mm = Column(Float, nullable=True)
    actual_weight_kg = Column(Float, nullable=True)
    dimensions_source = Column(String, default="")   # public_api / manual

    # ---- 采购 ----
    purchase_url = Column(String, default="")
    purchase_cost_cny = Column(Float, nullable=True)
    purchase_shipping_cny = Column(Float, nullable=True)
    purchase_quantity = Column(Integer, default=4)
    unit_price_cny = Column(Float, nullable=True)
    packaging_cost_per_unit_cny = Column(Float, default=1.0)
    shipping_method = Column(String, nullable=True)

    # ---- 计算结果（由 calculate_all 写入，只做展示与排序，不参与再计算）----
    fulfillment_fee_zar = Column(Float, nullable=True)
    success_fee_zar = Column(Float, nullable=True)
    volume_cbm = Column(Float, nullable=True)
    chargeable_weight_kg = Column(Float, nullable=True)
    unit_product_cost_cny = Column(Float, nullable=True)
    international_shipping_per_unit_cny = Column(Float, nullable=True)
    cost_to_sa_warehouse_zar = Column(Float, nullable=True)
    total_cost_zar = Column(Float, nullable=True)
    profit_zar = Column(Float, nullable=True)
    profit_margin = Column(Float, nullable=True)

    # ---- 流程状态 ----
    status = Column(String, default="待核算", index=True)   # 待核算 / 合格 / 不合格 / 已建产品
    selected = Column(Boolean, default=False)
    enrich_status = Column(String, default="")             # "" / ok / failed
    enrich_message = Column(String, default="")
    note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
