import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Float, Integer, Boolean, DateTime, Text, Enum as SAEnum,
    ForeignKey
)
from sqlalchemy.orm import relationship
from database import Base
import enum


def generate_uuid():
    return str(uuid.uuid4())


class ShippingMethod(str, enum.Enum):
    AIR_REGULAR = "空运普货"
    AIR_BATTERY = "空运带电"
    SEA_REGULAR = "海运普货"
    SEA_BATTERY = "海运带电"


class SelectionStatus(str, enum.Enum):
    DATA_INCOMPLETE = "数据待补充"
    CATEGORY_PENDING = "待确认品类"
    QUALIFIED = "合格选品"
    NOT_RECOMMENDED = "不建议选品"


class Product(Base):
    __tablename__ = "products"

    id = Column(String, primary_key=True, default=generate_uuid)
    product_no = Column(Integer, autoincrement=True)
    recorded_at = Column(DateTime, default=datetime.utcnow)
    note = Column(Text, default="")

    # Takealot fields
    takealot_url = Column(String, default="")
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
