# -*- coding: utf-8 -*-
"""根据已确认的装箱/发货记录生成 ERP（鲲鹏 JF WMS）入仓模板。

这份 xlsx 是**导入 OMS 建入库单**用的，不是给人看的：
A 标识号 / B 箱子数量 / C 自定义箱号 / I SKU / J SKU数量/箱。

OMS 的规则是「标识号相同的行 = 同一个箱子」，所以标识号必须**按箱递增**、
同一箱的多个 SKU 共用同一个标识号。早先按行递增（一行一个标识号），
混装箱会被拆成好几个箱子，箱唛上「SKU / 单箱数量」那张表就成了每箱一个 SKU。
"""

import html
import os
import re
import zipfile
from pathlib import Path


BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE = BASE_DIR / "inbound_template.xlsx"
SHEET = "xl/worksheets/sheet1.xml"
ROW_RE = re.compile(r'<row\b(?=[^>]*\br="(?P<number>\d+)")[^>]*(?:/>|>.*?</row\s*>)', re.S)
DIMENSION_RE = re.compile(r'(<dimension\b[^>]*\bref=")(?P<start_col>[A-Z]+)(?P<start_row>\d+):(?P<end_col>[A-Z]+)\d+(")')

# OMS 箱唛每张只放得下 5 行 SKU（实测：表头 + 5 行刚好贴到 289pt 页底），
# 第 6 行起会另起一页，而那一页没有箱号、没有条码 —— 贴到箱子上认不出是哪箱。
MAX_SKUS_PER_BOX = 5


def _plain_cell(coordinate: str, value, *, text: bool = False) -> str:
    """只写入值，不添加单元格样式或格式。"""
    if text:
        safe = html.escape(str(value), quote=False)
        return f'<c r="{coordinate}" t="inlineStr"><is><t>{safe}</t></is></c>'
    return f'<c r="{coordinate}"><v>{int(value)}</v></c>'


def _data_row(index: int, box_seq: int, sku: str, quantity: int) -> str:
    """A 标识号、B 箱子数量、C 自定义箱号、I SKU、J SKU数量/箱。

    C 写成**数字**不是文本 "01"：用户手工维护、且真的导入成功过的那份
    `海运_ERP入仓模板_2026-10-07.xlsx` 里 C 列就是 1、2、3 这种数字。
    之前按「至少 2 个字符」写成文本 "01"，类型和那份对不上，没必要冒这个险。
    """
    return (
        f'<row r="{index}" spans="1:10">'
        f'{_plain_cell(f"A{index}", box_seq)}'
        f'{_plain_cell(f"B{index}", 1)}'
        f'{_plain_cell(f"C{index}", box_seq)}'
        f'{_plain_cell(f"I{index}", sku, text=True)}'
        f'{_plain_cell(f"J{index}", quantity)}'
        '</row>'
    )


def flatten_boxes(boxes):
    """[(箱序, [(sku, 每箱数量), ...]), ...] → [(箱序, sku, 数量), ...]。

    一箱多个 SKU 会展开成多行，但**箱序不变** —— 这就是 OMS 认箱的依据。
    """
    out = []
    for box_seq, items in boxes:
        for sku, quantity in items:
            out.append((int(box_seq), str(sku).strip(), int(quantity)))
    return out


def generate_inbound(boxes, output: Path) -> None:
    """boxes: [(箱序, [(sku, 每箱数量), ...]), ...]，箱序从 1 连续排。

    箱序直接当标识号用，所以调用方要保证跨多个装箱单时**连续累加**，
    否则两票货会共用标识号、在 OMS 里被并成同一批箱子。
    """
    rows = flatten_boxes(boxes)
    with zipfile.ZipFile(TEMPLATE) as source:
        sheet_xml = source.read(SHEET).decode("utf-8")

        # 清除模板或上次导出的数据行，只保留表头及其原始 XML/格式。
        def keep_header(match):
            return match.group(0) if int(match.group("number")) == 1 else ""

        sheet_xml = ROW_RE.sub(keep_header, sheet_xml)
        sheet_data_end = sheet_xml.find("</sheetData>")
        if sheet_data_end < 0:
            raise ValueError("入仓模板缺少工作表数据")

        data_rows = "".join(
            _data_row(index, box_seq, sku, quantity)
            for index, (box_seq, sku, quantity) in enumerate(rows, start=2)
        )
        sheet_xml = sheet_xml[:sheet_data_end] + data_rows + sheet_xml[sheet_data_end:]

        last_row = max(1, len(rows) + 1)
        sheet_xml, updated = DIMENSION_RE.subn(
            lambda match: (
                match.group(1)
                + match.group("start_col")
                + match.group("start_row")
                + ":"
                + match.group("end_col")
                + str(last_row)
                + match.group(5)
            ),
            sheet_xml,
            count=1,
        )
        if not updated:
            raise ValueError("入仓模板缺少表格范围信息")

        replacement = sheet_xml.encode("utf-8")
        with zipfile.ZipFile(output, "w") as target:
            for entry in source.infolist():
                payload = replacement if entry.filename == SHEET else source.read(entry.filename)
                target.writestr(entry, payload)
