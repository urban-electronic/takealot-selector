"""Takealot 官方 Product Label 的复刻版 —— 一个 SKU 一张 40x30mm。

版式不是猜的，是从官方下载的 product_labels.pdf 里量出来的：
全部 Helvetica 8pt，产品名 3 行在上，EAN-13 横排、最小条宽 0.33mm（0.935pt）、
数据条高 7.61mm（21.581pt），起/中/止六根护线 31.181pt（伸到数字那一行），
13 位数字按 1-6-6 分组内嵌在条码下方，条码左侧还有 M / P 两个标记。
整块内容 34.3 x 21.8mm。

「跟官方裁剪后一模一样」是逐条比对过的：同 SKU 下 17 段文字、30 个条码矩形
x/y/宽/高全部一致（残差 ≤0.04pt 是官方自己写 PDF 时的舍入），
203 / 300 / 400 / 600 / 1200 dpi 全部扫得出。

官方那份是一张 A4 只印一个标签（左下角）。这里两种出法：
- generate_label_files：每个 SKU 一个 PDF，页面就是 40x30，标签打印机直接打（默认）
- generate_labels：A4 拼版，40x30 排 5 列 x 9 行 = 45 张/页，带裁切角标

注意：官方内容宽 34.3mm，塞进 30mm 宽的竖版要缩到 0.78 倍，条宽会降到 0.258mm
（低于 EAN-13 的 0.264mm 下限）。要保证扫码余量，用 40x30。
"""

import re

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

# --- 官方 product_labels.pdf 量出来的版式，单位 pt，原点在内容左下角 ---
# 数值全部来自官方文件第 1 页第 1 张标签的实测（x0=17.86, y0=735.09 为内容块原点），
# 不是估算。改动这里必须同步 src/utils/labelPdf.ts，否则前后端出的标签会不一样。
CONTENT_W = 97.24
CONTENT_H = 61.90
NAME_SIZE = 8.0
NAME_BASELINES = (53.3, 45.3, 37.3)  # 3 行，从上到下
# 产品名折行宽度：比内容宽多 0.5pt 的容差。
# 不能写成 CONTENT_W - BAR_X（88.82）—— 官方第 2 行实测 97.35pt，比内容宽还多 0.11，
# 照样排在同一行；用 88.82 会提前折行，三行的断词位置就跟官方对不上了。
NAME_MAX_WIDTH = CONTENT_W + 0.5
DIGITS_BASELINE = 2.37
BAR_X = 8.42          # 官方首条（起始护线第一根）的 x
BAR_TOP = 31.64       # 条码**顶部**（从内容块底部算），数据条和护线顶对齐
BAR_WIDTH = 0.935     # pt = 0.33mm，95 个模块正好铺满 88.825pt
BAR_HEIGHT = 21.581   # 数据条高（7.61mm）
GUARD_HEIGHT = 31.181  # 护线高：比数据条长 9.6pt，官方版式里伸到数字下方
MARK_X = 0.0
MARK_M_BASELINE = 23.13
MARK_P_BASELINE = 14.63
# 数字分组位置（1-6-6）
DIGIT_X = (0.93, 17.52, 61.49)

# EAN-13 的 95 个模块里，哪些是护线（起始 101 / 中间 01010 / 终止 101）。
# 护线要画得比数据条长，这是官方标签最容易被漏掉的一处细节。
GUARD_MODULES = frozenset((0, 2, 46, 48, 92, 94))

# EAN-13 编码表（标准，与官方条码逐条实测一致）
EAN_L = ('0001101', '0011001', '0010011', '0111101', '0100011',
         '0110001', '0101111', '0111011', '0110111', '0001011')
EAN_G = ('0100111', '0110011', '0011011', '0100001', '0011101',
         '0111001', '0000101', '0010001', '0001001', '0010111')
EAN_R = ('1110010', '1100110', '1101100', '1000010', '1011100',
         '1001110', '1010000', '1000100', '1001000', '1110100')
EAN_PARITY = ('LLLLLL', 'LLGLGG', 'LLGGLG', 'LLGGGL', 'LGLLGG',
              'LGGLLG', 'LGGGLL', 'LGLGLG', 'LGLGGL', 'LGGLGL')

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
    """一个产品 → 每个 SKU 一条标签数据，名字统一用产品英文名（跟官方一致）。

    `cn` 是中文简写（chinese_product_name 按 SKU 顺序一一对应），只进文件名，
    不印在标签上 —— 光看 13 位数字分不出是哪个品。
    """
    name = (getattr(product, 'product_name', '') or '').strip()
    skus = split_skus(getattr(product, 'sku', ''))
    raw_cn = (getattr(product, 'chinese_product_name', '') or '').strip()
    if len(skus) == 1:
        # 单 SKU 时整串都是这一个品的中文名，不能按空格切 ——
        # '6pcs tapo垃圾袋' 里那个空格是名字的一部分，切开就对不上了。
        labels = [raw_cn]
    else:
        labels = split_skus(raw_cn)
        if len(labels) != len(skus):
            labels = [''] * len(skus)
    return [
        {'id': getattr(product, 'id', ''), 'sku': sku, 'name': name, 'cn': cn, 'copies': copies}
        for sku, cn in zip(skus, labels)
    ]


def safe_filename(text):
    """SKU 可能带斜杠/空格，落到文件名上要清掉，不然打包和打印都会出问题。"""
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', '_', str(text or '').strip())
    return cleaned or 'label'


# 规格名占位值（没对上真实颜色/尺码时会出现），不能拿来当文件名
PLACEHOLDER_LABEL = re.compile(r'^(主商品|规格待识别|规格待确认|历史分支)$')


def label_filename(sku, cn=''):
    """一个 SKU 一个文件：`9902348623136_皮革黑色包.pdf`。

    13 位数字光看分不出是哪个品，把中文简写拼在后面，打印时一眼能认。
    """
    short = re.sub(r'[\\/:*?"<>|\s]+', '_', str(cn or '').strip()).strip('_')
    if not short or PLACEHOLDER_LABEL.match(short):
        return '%s.pdf' % safe_filename(sku)
    return '%s_%s.pdf' % (safe_filename(sku), short[:20])


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
    """按宽度折行，真的放不下才加省略号。

    早先的版本只要凑满 max_lines 就在最后一行补 "..."，于是「正好折成 3 行、
    第三行又刚好填满」的产品名会被截掉最后一个词——名字本来完整，印出来却带省略号。
    这里用 dropped 标记区分「被截断」和「刚好填满」。
    """
    words = _latin1(text).split()
    lines, cur = [], ''
    dropped = False
    for word in words:
        trial = (cur + ' ' + word).strip()
        if stringWidth(trial, FONT, size) <= max_width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = ''
        if len(lines) == max_lines:
            dropped = True
            break
        cur = word
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if dropped and lines:
        last = lines[-1]
        while stringWidth(last + '...', FONT, size) > max_width and len(last) > 1:
            last = last[:-1]
        lines[-1] = last + '...'
    return lines


def ean13_modules(code):
    """13 位数字 → 95 个模块（1 = 黑条），EAN-13 标准结构：101 + 6 位 + 01010 + 6 位 + 101。"""
    parity = EAN_PARITY[int(code[0])]
    bits = '101'
    for i in range(6):
        digit = int(code[i + 1])
        bits += EAN_L[digit] if parity[i] == 'L' else EAN_G[digit]
    bits += '01010'
    for i in range(6):
        bits += EAN_R[int(code[i + 7])]
    bits += '101'
    return [1 if b == '1' else 0 for b in bits]


def barcode_rects(code):
    """条码的矩形列表 [(x, y, w, h)]，坐标原点在内容块左下角。

    连续的黑模块合并成一个矩形，但**护线和数据条不能合并** —— 两者高度不同。
    官方版式里护线（起/中/止）比数据条长 9.6pt，正好落在 13 位数字那一行，
    少了它标签会明显矮一截。
    """
    modules = ean13_modules(code)
    rects = []
    i = 0
    total = len(modules)
    while i < total:
        if not modules[i]:
            i += 1
            continue
        guard = i in GUARD_MODULES
        run = 1
        while i + run < total and modules[i + run] and (i + run in GUARD_MODULES) == guard:
            run += 1
        height = GUARD_HEIGHT if guard else BAR_HEIGHT
        rects.append((BAR_X + i * BAR_WIDTH, BAR_TOP - height, run * BAR_WIDTH, height))
        i += run
    return rects


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
    lines = _wrap(name, NAME_SIZE, NAME_MAX_WIDTH, len(NAME_BASELINES))
    for line, baseline in zip(lines, NAME_BASELINES):
        c.drawString(0, baseline, line)

    # 2) 条码：自己编码 + 画矩形。
    # 不用 createBarcodeDrawing 是因为它不给护线加长，出来的条码是等高的，
    # 跟官方版式差 9.6pt（护线要伸到 13 位数字那一行）。
    for bx, by, bw, bh in barcode_rects(code):
        c.rect(bx, by, bw, bh, stroke=0, fill=1)

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
    """把 items 过滤成 [(name, sku, cn, copies)]，条码不合法的单独记下来。"""
    usable, skipped = [], []
    for item in items:
        sku = str(item.get('sku') or '').strip()
        name = str(item.get('name') or '').strip()
        cn = str(item.get('cn') or '').strip()
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
        usable.append((name or sku, sku, cn, copies))
    return usable, skipped


def generate_label_files(items, output_dir, size=DEFAULT_SIZE):
    """每个 SKU 一个独立 PDF，页面就是标签本身（40x30），可直接丢给标签打印机。

    返回 (文件路径列表, 跳过列表, 总张数)。copies > 1 时同一个 SKU 的页面在一份 PDF 里
    重复，标签打印机连打就是多张，不用拆文件。
    """
    usable, skipped = _collect(items)
    label_w, label_h = LABEL_SIZES.get(size, LABEL_SIZES[DEFAULT_SIZE])
    files, total = [], 0
    for name, sku, cn, copies in usable:
        out = output_dir / label_filename(sku, cn)
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
    for name, sku, cn, copies in usable:
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
