"""标签版式回归：尺寸、张数、跳过规则。"""

from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth

from services.label_pdf import (
    FONT,
    LABEL_SIZES,
    _wrap,
    barcode_width_mm,
    generate_label_files,
    generate_labels,
    grid_shape,
    is_ean13,
    split_skus,
)
from tests.pdfmeta import page_count, page_sizes

SKU_A = '9902348621873'
SKU_B = '9902348623136'


def _page_size(path):
    """返回 (宽mm, 高mm, 页数)。"""
    data = Path(path).read_bytes()
    width, height = page_sizes(data)[0]
    return width / 72 * 25.4, height / 72 * 25.4, page_count(data)


def _build(tmp_path, size='40x30', items=None):
    items = items or [{'sku': SKU_A, 'name': 'Test Product'}]
    out = Path(tmp_path) / 'labels.pdf'
    count, skipped = generate_labels(items, out, size=size)
    return out, count, skipped


@pytest.mark.parametrize('size', ['40x30', '30x40'])
def test_sheet_is_a4(tmp_path, size):
    """拼版页始终是 A4，标签尺寸只影响格子。"""
    out, _, _ = _build(tmp_path, size)
    w, h, _ = _page_size(out)
    assert round(w) == round(A4[0] / 72 * 25.4)
    assert round(h) == round(A4[1] / 72 * 25.4)


def test_one_sku_one_label(tmp_path):
    """一个 SKU 默认只出一张，不重复。"""
    _, count, _ = _build(tmp_path)
    assert count == 1


def test_copies_repeats_same_sku(tmp_path):
    """copies 让同一个 SKU 连续出多张（按发货数量贴）。"""
    _, count, skipped = _build(tmp_path, items=[{'sku': SKU_A, 'name': 'A', 'copies': 3}])
    assert (count, skipped) == (3, [])


def test_grid_fills_a4_40x30():
    """40x30（官方尺寸）：A4 每页 5 列 x 9 行 = 45 张。"""
    cols, rows, w, h = grid_shape('40x30')
    assert (cols, rows) == (5, 9)
    assert (round(w / 2.834645), round(h / 2.834645)) == (40, 30)


def test_grid_fills_a4_30x40():
    """30x40 竖版：A4 每页 6 列 x 7 行 = 42 张。"""
    cols, rows, _, _ = grid_shape('30x40')
    assert (cols, rows) == (6, 7)


def test_40x30_keeps_official_bar_width():
    """官方内容宽 34.3mm，40x30 里能 1:1 放下，条宽保持 0.33mm。"""
    assert round(barcode_width_mm('40x30'), 3) == 0.33


def test_30x40_bar_width_below_standard():
    """30x40 要缩到 0.78 倍，条宽跌破 EAN-13 下限 0.264mm —— 别当默认。"""
    assert barcode_width_mm('30x40') < 0.264


def test_overflow_spills_to_next_page(tmp_path):
    """超过一页张数自动翻页，不丢标签。"""
    out, count, _ = _build(tmp_path, items=[{'sku': SKU_A, 'name': 'A', 'copies': 50}])
    assert count == 50
    assert _page_size(out)[2] == 2


def test_bad_barcode_is_skipped(tmp_path):
    """条码不合法/为空直接跳过，不生成扫不出的废纸。"""
    _, count, skipped = _build(
        tmp_path,
        items=[
            {'sku': '', 'name': 'no sku', 'id': 7},
            {'sku': '12345', 'name': 'short'},
            {'sku': '9902348621874', 'name': 'bad checksum'},
            {'sku': SKU_B, 'name': 'good'},
        ],
    )
    assert count == 1
    assert len(skipped) == 3


@pytest.mark.parametrize(
    'code,ok',
    [
        (SKU_A, True),
        (SKU_B, True),
        ('9902348621874', False),  # 校验位错
        ('990234862187', False),   # 12 位
        ('', False),
        ('abcdefghijklm', False),
    ],
)
def test_is_ean13(code, ok):
    assert is_ean13(code) is ok


def test_sizes_registry():
    assert LABEL_SIZES['40x30'] == (40, 30)
    assert LABEL_SIZES['30x40'] == (30, 40)


@pytest.mark.parametrize(
    'raw,expected',
    [
        (SKU_A, [SKU_A]),
        (f'{SKU_A} {SKU_B}', [SKU_A, SKU_B]),
        (f'{SKU_A},{SKU_B}', [SKU_A, SKU_B]),
        (f'{SKU_A}，{SKU_B}', [SKU_A, SKU_B]),
        ('', []),
    ],
)
def test_split_skus(raw, expected):
    """多规格产品的 sku 是一整串，必须拆开——整串校验 EAN-13 必然失败。"""
    assert split_skus(raw) == expected


def test_single_files_are_40x30(tmp_path):
    """single 模式：每个 SKU 一个 PDF，页面就是 40x30，标签打印机直接打。"""
    files, skipped, total = generate_label_files(
        [{'sku': SKU_A, 'name': 'A'}, {'sku': SKU_B, 'name': 'B'}], tmp_path, size='40x30'
    )
    assert [f.name for f in files] == [f'{SKU_A}.pdf', f'{SKU_B}.pdf']
    assert (skipped, total) == ([], 2)
    for f in files:
        w, h, pages = _page_size(f)
        # pt -> mm 有浮点误差（40mm 算出来 39.99999），按 0.1mm 容差比
        assert (round(w, 1), round(h, 1), pages) == (40.0, 30.0, 1)


def _filled_line(size, width):
    """往一行里反复塞同一个词直到再塞就超宽，用来构造「刚好填满」的样本。"""
    line = 'X'
    while stringWidth(line + ' X', FONT, size) <= width:
        line += ' X'
    return line


def test_wrap_keeps_full_last_line():
    """正好折成 3 行、第三行刚好填满时不许加省略号。

    早先的实现只要凑满 3 行就无条件在末行补 "..."，于是产品名明明完整、印出来却
    被截掉最后一个词——40x30 的标签上 34.3mm 宽、8pt 字号，长名字很容易正好占满 3 行。
    这条用例在 2026-10-22 的并行重写（改用 tests/pdfmeta.py 量页面）里被整段丢掉，
    文档却还在引用它，所以原样补回。
    """
    size, width = 8.0, 60.0
    line = _filled_line(size, width)
    lines = _wrap(' '.join([line] * 3), size, width, 3)
    assert lines == [line, line, line]
    assert not lines[-1].endswith('...')


def test_wrap_marks_dropped_words():
    """真的放不下、有词被丢掉时，末行必须以省略号结尾（不能假装印全了）。"""
    size, width = 8.0, 60.0
    line = _filled_line(size, width)
    lines = _wrap(' '.join([line] * 4), size, width, 3)
    assert len(lines) == 3
    assert lines[-1].endswith('...')
    assert all(stringWidth(text, FONT, size) <= width for text in lines)
