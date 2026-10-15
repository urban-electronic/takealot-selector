# -*- coding: utf-8 -*-
"""根据已确认的装箱/发货记录生成 ERP 入仓模板。"""

import html
import os
import re
import zipfile
from pathlib import Path


BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE = BASE_DIR / "inbound_template.xlsx"
SHEET = "xl/worksheets/sheet1.xml"
ROW_RE = re.compile(r'<row\b(?=[^>]*\br="(?P<number>\d+)")[^>]*(?:/>|>.*?</row\s*>)', re.S)
DIMENSION_RE = re.compile(r'(<dimension\b[^>]*\bref=")[^"]*(")')


def _plain_cell(coordinate: str, value, *, text: bool = False) -> str:
    """只写入值，不添加单元格样式或格式。"""
    if text:
        safe = html.escape(str(value), quote=False)
        return f'<c r="{coordinate}" t="inlineStr"><is><t>{safe}</t></is></c>'
    return f'<c r="{coordinate}"><v>{int(value)}</v></c>'


def _data_row(index: int, identifier: int, line) -> str:
    """A 标识号、B 箱数、C 自定义箱号、I SKU、J 每箱 SKU 数量。"""
    return (
        f'<row r="{index}" spans="1:10">'
        f'{_plain_cell(f"A{index}", identifier)}'
        f'{_plain_cell(f"B{index}", 1)}'
        f'{_plain_cell(f"C{index}", identifier)}'
        f'{_plain_cell(f"I{index}", line.sku, text=True)}'
        f'{_plain_cell(f"J{index}", line.total_quantity)}'
        '</row>'
    )


def generate_inbound(rows, output: Path) -> None:
    """每张装箱单对应一个箱子；同一装箱单的所有 SKU 共用箱号和标识号。"""
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
            _data_row(index, identifier, line)
            for index, (identifier, line) in enumerate(rows, start=2)
        )
        sheet_xml = sheet_xml[:sheet_data_end] + data_rows + sheet_xml[sheet_data_end:]

        dimension_ref = f"A1:J{max(1, len(rows) + 1)}"
        sheet_xml, updated = DIMENSION_RE.subn(
            lambda match: match.group(1) + dimension_ref + match.group(2),
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
