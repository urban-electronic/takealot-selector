"""Store-scoped listing preparation. No pricing, seller writes or inferred facts."""
from __future__ import annotations

import io
import ipaddress
import json
import math
import re
import uuid
import zipfile
from datetime import datetime, timezone
from urllib.parse import urlsplit

from openpyxl import Workbook, load_workbook
from sqlalchemy import Column, Integer, String, Text, UniqueConstraint

from database import Base


class ListingDraft(Base):
    __tablename__ = 'listing_drafts'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    store_id = Column(String, nullable=False, index=True)
    internal_code = Column(String, nullable=False)
    source_file = Column(String, nullable=False)
    source_row = Column(Integer, nullable=False)
    payload = Column(Text, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    state = Column(String, nullable=False, default='draft')
    reviewed_at = Column(String, nullable=True)
    updated_at = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint('store_id', 'internal_code', name='uq_listing_store_code'),)


MEASUREMENTS = ('packaged_length_cm', 'packaged_width_cm', 'packaged_height_cm', 'packaged_weight_kg')
TEXT_FIELDS = ('english_name', 'title', 'subtitle', 'description', 'whats_in_box', 'brand',
               'brand_mode', 'category_id', 'category_path', 'major_colour', 'material',
               'features', 'usage', 'notes', 'sales_unit', 'country_of_origin')
EDITABLE_FIELDS = set(TEXT_FIELDS) | set(MEASUREMENTS) | {'other_colours', 'image_urls'}
ALIASES = {
    'internal_code': ('内部编号', 'internal code', 'internal_code'),
    'supplier_code': ('原货号', '原表编号', '产品编号', 'supplier code', 'sku'),
    'raw_name': ('产品名称', '产品名称原文', '产品名称（原文）', '产品名称（原文保留）', '原始品名', 'raw title', 'raw_name'),
    'english_name': ('完整英文品名', '产品英文名', '英文标题草稿', 'english name'),
    'title': ('主标题', 'product title', 'title'),
    'subtitle': ('副标题', 'subtitle'),
    'description': ('产品描述', 'description'),
    'whats_in_box': ("what's in the box", 'what in the box', '包装清单'),
    'packaged_length_cm': ('包装后长(cm)', '包装后长（cm）', 'packaged length (cm)'),
    'packaged_width_cm': ('包装后宽(cm)', '包装后宽（cm）', 'packaged width (cm)'),
    'packaged_height_cm': ('包装后高(cm)', '包装后高（cm）', 'packaged height (cm)'),
    'packaged_weight_kg': ('包装后重量(kg)', '包装后重量（kg）', 'packaged weight (kg)'),
    'material': ('主材质', 'material'),
    'colour_candidates': ('原文颜色', '颜色', 'colour', 'color'),
    'major_colour': ('major colour', 'major color', '主色'),
    'other_colours': ('other colours', 'other colors', '其他颜色'),
    'features': ('核心功能', '卖点事实', 'features'),
    'usage': ('适用场景', 'usage'),
    'notes': ('核对提示', '补充备注', '供应商备注', 'notes'),
    'brand': ('品牌', 'brand'),
    'category_path': ('后台分类', 'category path'),
    'category_id': ('后台分类id', 'category id'),
    'sales_unit': ('销售单位', 'sales unit'),
}
COLOURS = {'黑': 'Black', '黑色': 'Black', '白': 'White', '白色': 'White', '红': 'Red', '红色': 'Red',
           '蓝': 'Blue', '蓝色': 'Blue', '绿': 'Green', '绿色': 'Green', '黄': 'Yellow', '黄色': 'Yellow',
           '灰': 'Grey', '灰色': 'Grey', 'gray': 'Grey', '棕色': 'Brown', '橙色': 'Orange',
           '粉色': 'Pink', '紫色': 'Purple', '银色': 'Silver', '金色': 'Gold', '透明': 'Clear',
           '米白': 'Cream', '米白色': 'Cream', '米色': 'Beige', '多色': 'Multicolour'}
COLOURS.update({name.lower(): name for name in ('Black', 'White', 'Red', 'Blue', 'Green', 'Yellow',
    'Grey', 'Brown', 'Orange', 'Pink', 'Purple', 'Silver', 'Gold', 'Clear', 'Cream', 'Beige', 'Multicolour')})
TRUNCATED = re.compile(r'…|\.{3,}')
CHINESE = re.compile(r'[\u3400-\u9fff]')
MAX_ROWS = 5000
MAX_BYTES = 10 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def text(value):
    return '' if value is None else str(value).strip()


def normal(value):
    return re.sub(r'[\s_\-（）()/*:*：]', '', text(value)).lower()


def colour(value):
    val = text(value)
    return COLOURS.get(val, COLOURS.get(val.lower(), val))


def colours(value):
    parts = value if isinstance(value, list) else re.split(r'[/,，、;；\n]+', text(value))
    return list(dict.fromkeys(colour(x) for x in parts if text(x)))


def blank_payload():
    return dict(raw_name='', supplier_code='', english_name='', title='', subtitle='', description='',
                whats_in_box='', brand='', brand_mode='', category_id='', category_path='',
                major_colour='', other_colours=[], colour_candidates=[], image_urls=[], material='',
                features='', usage='', notes='', sales_unit='原货盘销售包装', country_of_origin='China',
                import_conflicts=[], **dict.fromkeys(MEASUREMENTS))


def number(value, label):
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError(f'{label}必须为大于0的数字')
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label}必须只填数字，单位以列名为准') from None
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f'{label}必须为大于0的有限数字')
    return result


def public_image_url(value):
    try:
        u = urlsplit(value)
        host = (u.hostname or '').lower()
        if u.scheme != 'https' or not host or u.username or u.password or u.port not in (None, 443):
            return False
        if host == 'localhost' or '.' not in host or host.endswith(('.local', '.internal', '.localhost')):
            return False
        try:
            if not ipaddress.ip_address(host).is_global:
                return False
        except ValueError:
            pass
        return True
    except ValueError:
        return False


def issues(payload):
    """Only listing facts gate review. Missing costs/prices deliberately do not."""
    result = []

    def add(field, message):
        result.append(dict(field=field, message=message))

    for field, label in [('title', '英文主标题'), ('subtitle', '英文副标题'),
                         ('description', '英文描述（含Key Features）'), ('whats_in_box', '包装清单'),
                         ('category_id', '后台类目ID'), ('category_path', '后台类目路径'),
                         ('major_colour', 'Major Colour'), ('sales_unit', '销售单位'),
                         ('country_of_origin', '产地')]:
        val = text(payload.get(field))
        if not val:
            add(field, f'请补充{label}')
        elif field in ('title', 'subtitle', 'description', 'whats_in_box', 'major_colour'):
            if CHINESE.search(val):
                add(field, f'{label}需要英文')
            if TRUNCATED.search(val):
                add(field, f'{label}存在截断，请补全')
    if len(text(payload.get('title'))) > 75:
        add('title', '主标题最多75个字符')
    if len(text(payload.get('subtitle'))) > 110:
        add('subtitle', '副标题最多110个字符')
    for field in MEASUREMENTS:
        try:
            if number(payload.get(field), field) is None:
                add(field, '请补充单个销售包装的实测重尺')
        except ValueError as e:
            add(field, str(e))
    if TRUNCATED.search(text(payload.get('raw_name'))) and not text(payload.get('english_name')):
        add('english_name', '原英文品名被截断，请填写供应商补全的完整英文品名')
    english_name = text(payload.get('english_name'))
    if english_name and (CHINESE.search(english_name) or TRUNCATED.search(english_name)):
        add('english_name', '完整英文品名须补全，不能包含中文或省略号')
    if text(payload.get('description')) and not re.search(r'key\s+(?:selling\s+)?features', payload['description'], re.I):
        add('description', '英文描述须包含Key Features小标题和真实卖点')
    if payload.get('brand_mode') not in ('branded', 'generic'):
        add('brand_mode', '请选择品牌商品或无品牌商品')
    elif payload['brand_mode'] == 'branded' and not text(payload.get('brand')):
        add('brand', '品牌商品需要填写真实品牌')
    elif payload['brand_mode'] == 'generic' and text(payload.get('brand')):
        add('brand', '无品牌模式下不能同时填写品牌，请确认商品类型')
    if any(CHINESE.search(x) for x in payload.get('other_colours', [])):
        add('other_colours', 'Other Colours需要映射为英文颜色')
    if colour(payload.get('major_colour')) in payload.get('other_colours', []):
        add('other_colours', 'Other Colours不能重复包含Major Colour')
    urls = payload.get('image_urls', [])
    if len(urls) < 5:
        add('image_urls', '正式上架需1张主图及至少4张详情图；原图至少3张用于后续制作')
    elif len(set(urls)) != len(urls) or not all(public_image_url(u) for u in urls):
        add('image_urls', '图片须为互不重复的公网HTTPS链接')
    if payload.get('import_conflicts'):
        add('import_conflicts', '回填与已有资料存在差异，请逐字段核对后保存')
    return result


def parse_table(data, filename):
    if not filename.lower().endswith('.xlsx'):
        raise ValueError('此入口读取供应商回填.xlsx；原始PDF解析另行接入')
    if len(data) > MAX_BYTES:
        raise ValueError('单个回填表不能超过10MB')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(x.file_size for x in z.infolist()) > 60 * 1024 * 1024:
                raise ValueError('表格解压后过大，请拆分上传')
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    except (zipfile.BadZipFile, KeyError, OSError):
        raise ValueError('无法读取Excel，请另存为标准.xlsx后重试') from None
    try:
        candidates = []
        for sheet in wb:
            if sheet.max_row is None or sheet.max_column is None:
                sheet.calculate_dimension(force=True)
            if sheet.max_row > MAX_ROWS + 20 or sheet.max_column > 300:
                raise ValueError('工作表范围过大，请删除空白尾行/尾列或拆分后重试')
            for rn, row in enumerate(sheet.iter_rows(max_row=20, values_only=True), 1):
                mapping = {}
                for ci, val in enumerate(row):
                    label = normal(val)
                    for field, aliases in ALIASES.items():
                        if label and any(label.startswith(normal(a)) for a in aliases):
                            mapping.setdefault(field, ci)
                            break
                if 'internal_code' in mapping and 'raw_name' in mapping:
                    candidates.append((len(mapping), sheet, rn, mapping))
        if not candidates:
            raise ValueError('未找到“内部编号”和“产品名称”表头，请使用已发出的供应商回填表')
        _, sheet, header, mapping = max(candidates, key=lambda c: c[0])
        rows, seen = [], set()
        for rn, values in enumerate(sheet.iter_rows(min_row=header + 1, values_only=True), header + 1):
            if not any(v is not None and text(v) for v in values):
                continue
            code = text(values[mapping['internal_code']])
            if not code:
                raise ValueError(f'第{rn}行缺少内部编号，未导入任何行')
            if code in seen:
                raise ValueError(f'第{rn}行内部编号重复：{code}，未导入任何行')
            if len(code) > 100:
                raise ValueError(f'第{rn}行内部编号过长')
            seen.add(code)
            payload = blank_payload()
            for field, ci in mapping.items():
                if field == 'internal_code':
                    continue
                val = values[ci] if ci < len(values) else None
                if isinstance(val, str) and val.startswith('='):
                    raise ValueError(f'第{rn}行含公式，请将回填数据粘贴为数值后重试')
                payload[field] = number(val, f'第{rn}行{field}') if field in MEASUREMENTS else text(val)
            for field in ('other_colours', 'colour_candidates'):
                payload[field] = colours(payload.get(field))
            payload['major_colour'] = colour(payload.get('major_colour'))
            if not payload['major_colour'] and len(payload['colour_candidates']) == 1:
                # Multiple colour names do not encode surface-area ranking.
                only = payload['colour_candidates'][0]
                if only != 'Multicolour':
                    payload['major_colour'] = only
            complete = payload.get('english_name') or payload['raw_name']
            if complete and not CHINESE.search(complete) and not TRUNCATED.search(complete):
                payload['english_name'] = complete
                if not payload.get('title') and len(complete) <= 75:
                    payload['title'] = complete
            rows.append(dict(internal_code=code, source_file=filename, source_row=rn, payload=payload))
            if len(rows) > MAX_ROWS:
                raise ValueError('单表最多5000款，请拆分上传')
        if not rows:
            raise ValueError('表格没有商品行，未更改已有草稿')
        return rows
    finally:
        wb.close()


def merge_import(existing, incoming):
    """Fill gaps only; preserve reviewed/manual facts and surface disagreements."""
    result = dict(existing)
    conflicts = list(existing.get('import_conflicts', []))
    for field, value in incoming.items():
        if field in ('import_conflicts', 'sales_unit', 'country_of_origin') or value in (None, '', []):
            continue
        old = result.get(field)
        if field in ('raw_name', 'supplier_code') and old not in (None, '') and old != value:
            raise ValueError('内部编号对应的原货号或原品名发生变化，请先核对编号，未覆盖已有商品')
        if old in (None, '', []):
            result[field] = value
        elif old != value:
            item = dict(field=field, current=old, incoming=value)
            if item not in conflicts:
                conflicts.append(item)
    result['import_conflicts'] = conflicts
    return result


def serialize(row):
    payload = json.loads(row.payload)
    problems = issues(payload)
    return dict(id=row.id, internal_code=row.internal_code, source_file=row.source_file,
                source_row=row.source_row, revision=row.revision, state=row.state,
                reviewed_at=row.reviewed_at, updated_at=row.updated_at, payload=payload, issues=problems)


EXPORT_COLUMNS = [('内部编号', 'internal_code'), ('原货号', 'supplier_code'), ('原始品名', 'raw_name'),
                  ('完整英文品名', 'english_name'), ('Product Title', 'title'), ('Subtitle', 'subtitle'),
                  ('Description', 'description'), ("What's in the Box", 'whats_in_box'),
                  ('Brand', 'brand'), ('后台分类ID', 'category_id'), ('后台分类', 'category_path'),
                  ('Major Colour', 'major_colour'), ('Other Colours', 'other_colours'),
                  ('Packaged Length (cm)', 'packaged_length_cm'), ('Packaged Width (cm)', 'packaged_width_cm'),
                  ('Packaged Height (cm)', 'packaged_height_cm'), ('Packaged Weight (kg)', 'packaged_weight_kg'),
                  ('销售单位', 'sales_unit'), ('Country of Origin', 'country_of_origin'), ('图片链接', 'image_urls')]


def review_export(rows):
    """An explicitly labelled review pack, never a fabricated official loadsheet."""
    wb = Workbook()
    sheet = wb.active
    sheet.title = '已审核资料（非官方模板）'
    sheet.append([name for name, _ in EXPORT_COLUMNS])
    for row in rows:
        payload = dict(json.loads(row.payload), internal_code=row.internal_code)
        values = []
        for _, field in EXPORT_COLUMNS:
            value = payload.get(field)
            if isinstance(value, list):
                value = '\n'.join(value)
            # Supplier text must remain literal when opening the review pack.
            if isinstance(value, str) and value.startswith(('=', '+', '-', '@')):
                value = "'" + value
            values.append(value)
        sheet.append(values)
    output = io.BytesIO()
    wb.save(output)
    wb.close()
    return output.getvalue()
