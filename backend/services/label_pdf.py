"""Takealot 官方 Product Label 的复刻版 —— 一个 SKU 一张 40x30mm。

版式不是猜的，是从官方下载的 product_labels.pdf 里量出来的：
全部 Helvetica 8pt，产品名 3 行在上，EAN-13 横排、最小条宽 0.33mm（0.935pt）、
数据条高 7.6mm（21.59pt），13 位数字按 1-6-6 分组内嵌在条码下方，
条码左侧还有 M / P 两个标记。整块内容 34.3 x 21.8mm。

官方那份是一张 A4 只印一个标签（左下角）。这里两种出法：
- generate_label_files：每个 SKU 一个 PDF，页面就是 40x30，标签打印机直接打（默认）
- generate_labels：A4 拼版，40x30 排 5 列 x 9 行 = 45 张/页，带裁切角标

注意：官方内容宽 34.3mm，塞进 30mm 宽的竖版要缩到 0.78 倍，条宽会降到 0.258mm
（低于 EAN-13 的 0.264mm 下限）。要保证扫码余量，用 40x30。
"""

import re

from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

# --- 官方 product_labels.pdf 量出来的版式，单位 pt，原点在内容左下角 ---
CONTENT_W = 97.24
CONTENT_H = 61.90
NAME_SIZE = 8.0
NAME_BASELINES = (52.9, 44.9, 36.9)  # 3 行，从上到下
DIGITS_BASELINE = 2.0
BAR_X = 8.38
BAR_Y = 10.06
BAR_WIDTH = 0.935   # pt = 0.33mm
BAR_HEIGHT = 21.59  # pt = 7.6mm
MARK_X = 0.0
MARK_M_BASELINE = 22.4
MARK_P_BASELINE = 13.9
# 数字分组位置（1-6-6），官方是 0.8 / 17.5 / 61.4
DIGIT_X = (0.8, 17.5, 61.4)

LABEL_SIZES = {'40x30': (40, 30), '30x40': (30, 40)}
DEFAULT_SIZE = '40x30'
PRINT_MARGIN = 5 * mm
PAD = 1.6 * mm
FONT = 'Helvetica'

# SKU 与规格名都是「多值拼一个字段」，分隔符约定见 src/utils/variants.ts
SKU_SEP = re.compile(r'[\s,，;；]+')


def is_ean13(code):
    """13 位数字且校验位正确，才画得出可扫的 EAN-13。"""
    if not code or len(code) != 13 or not code.isdigit():
        return False
    body = code[:12]
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body))
    return (10 - total % 10) % 10 == int(code[12])


def split_skus(sku):
    """把「空格/逗号拼起来的多规格 SKU」拆开，一个 SKU 一张标签。"""
    return [s for s in SKU_SEP.split(sku or '') if s]


def variant_items(product, copies=1):
    """一个产品 → 每个 SKU 一条标签数据，名字统一用产品英文名（跟官方一致）。"""
    name = (getattr(product, 'product_name', '') or '').strip()
    return [
        {'id': getattr(product, 'id', ''), 'sku': sku, 'name': name, 'copies': copies}
        for sku in split_skus(getattr(product, 'sku', ''))
    ]


def safe_filename(text):
    """SKU 可能带斜杠/空格，落到文件名上要清掉，不然打包和打印都会出问题。"""
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', '_', str(text or '').strip())
    return cleaned or 'label'


def _latin1(text):
    """Helvetica 只认 Latin-1，不能编码的字符换成 ?，避免整页生成失败。"""
    out = []
    for ch in text:
        try:
            ch.encode('latin-1')
            out.append(ch)
        except UnicodeEncodeError:
            out.append('?')
    return ''.join(out)


def _wrap(text, size, max_width, max_lines=3):
    words = _latin1(text).split()
    lines, cur = [], ''
    for word in words:
        trial = (cur + ' ' + word).strip()
        if stringWidth(trial, FONT, size) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines:
        last = lines[-1]
        while stringWidth(last + '...', FONT, size) > max_width and len(last) > 1:
            last = last[:-1]
        if last != lines[-1]:
            lines[-1] = last + '...'
    return lines


def _draw_label(c, x, y, label_w, label_h, name, code):
    """在 (x, y) 画一张标签，左下角为原点。整块内容按官方比例等比缩放居中。"""
    scale = min((label_w - 2 * PAD) / CONTENT_W, (label_h - 2 * PAD) / CONTENT_H, 1.0)
    content_w = CONTENT_W * scale
    content_h = CONTENT_H * scale
    ox = x + (label_w - content_w) / 2.0
    oy = y + (label_h - content_h) / 2.0

    c.saveState()
    c.translate(ox, oy)
    c.scale(scale, scale)
    c.setFillColorRGB(0, 0, 0)

    # 1) 产品名 3 行，官方字号 8pt
    c.setFont(FONT, NAME_SIZE)
    lines = _wrap(name, NAME_SIZE, CONTENT_W - BAR_X, len(NAME_BASELINES))
    for line, baseline in zip(lines, NAME_BASELINES):
        c.drawString(0, baseline, line)

    # 2) 条码：横排，quiet=False 由版式自己留白（官方也是这么画的）
    drawing = createBarcodeDrawing(
        'EAN13',
        value=code,
        barWidth=BAR_WIDTH,
        barHeight=BAR_HEIGHT,
        humanReadable=False,
        quiet=False,
    )
    drawing.drawOn(c, BAR_X, BAR_Y)

    # 3) 13 位数字 1-6-6 分组，压在条码下方
    c.setFont(FONT, NAME_SIZE)
    for chunk, dx in zip((code[0], code[1:7], code[7:13]), DIGIT_X):
        c.drawString(dx, DIGITS_BASELINE, chunk)

    # 4) 条码左侧的 M / P 标记，官方版式里固定有这两个字母
    c.drawString(MARK_X, MARK_M_BASELINE, 'M')
    c.drawString(MARK_X, MARK_P_BASELINE, 'P')

    c.restoreState()


def _draw_cut_marks(c, x, y, label_w, label_h):
    """四角画裁切角标，剪的时候对着剪。"""
    c.saveState()
    c.setStrokeColorRGB(0.55, 0.55, 0.55)
    c.setLineWidth(0.25)
    tick = 2.0 * mm
    for cx, cy, sx, sy in (
        (x, y, 1, 1),
        (x + label_w, y, -1, 1),
        (x, y + label_h, 1, -1),
        (x + label_w, y + label_h, -1, -1),
    ):
        c.line(cx, cy, cx + sx * tick, cy)
        c.line(cx, cy, cx, cy + sy * tick)
    c.restoreState()


def grid_shape(size=DEFAULT_SIZE):
    """返回 (列数, 行数, 格子宽pt, 格子高pt)，供前端显示每页张数。"""
    label_w, label_h = LABEL_SIZES.get(size, LABEL_SIZES[DEFAULT_SIZE])
    usable_w = A4[0] - 2 * PRINT_MARGIN
    usable_h = A4[1] - 2 * PRINT_MARGIN
    # 0.5pt 容差：40mm x 5 正好等于可用宽度，浮点除法会算成 4.9998
    cols = int((usable_w + 0.5) // (label_w * mm))
    rows = int((usable_h + 0.5) // (label_h * mm))
    return max(1, cols), max(1, rows), label_w * mm, label_h * mm


def barcode_width_mm(size=DEFAULT_SIZE):
    """该尺寸下条码的最小条宽，用来判断会不会低于 EAN-13 的 0.264mm 下限。"""
    label_w, label_h = LABEL_SIZES.get(size, LABEL_SIZES[DEFAULT_SIZE])
    scale = min((label_w * mm - 2 * PAD) / CONTENT_W, (label_h * mm - 2 * PAD) / CONTENT_H, 1.0)
    return BAR_WIDTH * scale / mm


def _collect(items):
    """把 items 过滤成 [(name, sku, copies)]，条码不合法的单独记下来。"""
    usable, skipped = [], []
    for item in items:
        sku = str(item.get('sku') or '').strip()
        name = str(item.get('name') or '').strip()
        if not sku:
            skipped.append((item.get('id') or '', '没有条码'))
            continue
        if not is_ean13(sku):
            skipped.append((sku, '不是合法 EAN-13'))
            continue
        try:
            copies = int(item.get('copies') or 1)
        except (TypeError, ValueError):
            copies = 1
        copies = max(1, min(copies, 999))
        usable.append((name or sku, sku, copies))
    return usable, skipped


def generate_label_files(items, output_dir, size=DEFAULT_SIZE):
    """每个 SKU 一个独立 PDF，页面就是标签本身（40x30），可直接丢给标签打印机。

    返回 (文件路径列表, 跳过列表, 总张数)。copies > 1 时同一个 SKU 的页面在一份 PDF 里
    重复，标签打印机连打就是多张，不用拆文件。
    """
    usable, skipped = _collect(items)
    label_w, label_h = LABEL_SIZES.get(size, LABEL_SIZES[DEFAULT_SIZE])
    files, total = [], 0
    for name, sku, copies in usable:
        out = output_dir / ('%s.pdf' % safe_filename(sku))
        pdf = canvas.Canvas(str(out), pagesize=(label_w * mm, label_h * mm))
        pdf.setTitle(sku)
        for _ in range(copies):
            _draw_label(pdf, 0, 0, label_w * mm, label_h * mm, name, sku)
            pdf.showPage()
        pdf.save()
        files.append(out)
        total += copies
    return files, skipped, total


def generate_labels(items, output, size=DEFAULT_SIZE):
    """A4 拼版：40x30 每页 45 张，四角带裁切角标。

    返回 (已生成张数, 跳过的 [(sku, 原因)])。
    条码不合法的直接跳过，不让它变成一张扫不出来的废纸。
    """
    label_w, label_h = LABEL_SIZES.get(size, LABEL_SIZES[DEFAULT_SIZE])
    label_w *= mm
    label_h *= mm
    cols, rows, _, _ = grid_shape(size)
    per_page = cols * rows

    usable, skipped = _collect(items)
    if not usable:
        return 0, skipped

    grid_w = cols * label_w
    grid_h = rows * label_h
    origin_x = (A4[0] - grid_w) / 2.0
    origin_y = (A4[1] - grid_h) / 2.0

    pdf = canvas.Canvas(str(output), pagesize=A4)
    pdf.setTitle('Takealot Product Labels %s' % size)
    slot = 0
    for name, sku, copies in usable:
        for _ in range(copies):
            if slot and slot % per_page == 0:
                pdf.showPage()
            row, col = divmod(slot % per_page, cols)
            x = origin_x + col * label_w
            y = origin_y + (rows - 1 - row) * label_h
            _draw_label(pdf, x, y, label_w, label_h, name, sku)
            _draw_cut_marks(pdf, x, y, label_w, label_h)
            slot += 1
    pdf.showPage()
    pdf.save()
    return slot, skipped
