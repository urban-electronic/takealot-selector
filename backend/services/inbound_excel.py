# -*- coding: utf-8 -*-
"""根据已确认的装箱/发货记录生成 Takealot 入仓模板。"""

import os
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE = BASE_DIR / "inbound_template.xlsx"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
Q = lambda name: f"{{{MAIN}}}{name}"
ET.register_namespace("", MAIN)


def _cell(row, coordinate: str, value, *, text: bool = False) -> None:
    cell = ET.SubElement(row, Q("c"), {"r": coordinate, "s": "2"})
    if text:
        cell.set("t", "inlineStr")
        inline = ET.SubElement(cell, Q("is"))
        ET.SubElement(inline, Q("t")).text = str(value)
    else:
        ET.SubElement(cell, Q("v")).text = str(int(value))


def generate_inbound(rows, output: Path) -> None:
    """A 为装箱单顺序号，B 固定为 1，I 为 SKU，J 为该 SKU 的装箱总数量。"""
    with zipfile.ZipFile(TEMPLATE) as source:
        root = ET.fromstring(source.read("xl/worksheets/sheet1.xml"))
        sheet_data = root.find(Q("sheetData"))
        if sheet_data is None:
            raise ValueError("入仓模板缺少工作表数据")

        for row in list(sheet_data):
            if int(row.get("r", "0")) >= 2:
                sheet_data.remove(row)

        for index, (identifier, line) in enumerate(rows, start=2):
            row = ET.SubElement(sheet_data, Q("row"), {"r": str(index), "spans": "1:10"})
            _cell(row, f"A{index}", identifier)
            _cell(row, f"B{index}", 1)
            _cell(row, f"I{index}", line.sku, text=True)
            _cell(row, f"J{index}", line.total_quantity)

        dimension = root.find(Q("dimension"))
        if dimension is not None:
            dimension.set("ref", f"A1:J{max(1, len(rows) + 1)}")

        replacement = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        with zipfile.ZipFile(output, "w") as target:
            for entry in source.infolist():
                payload = replacement if entry.filename == "xl/worksheets/sheet1.xml" else source.read(entry.filename)
                target.writestr(entry, payload)
