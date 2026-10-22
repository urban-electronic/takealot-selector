"""读 PDF 的页面尺寸和页数，只用标准库。

为什么不用 pymupdf：它只是测试想量一下页面大小，却被当成运行依赖塞进
backend/requirements.txt 才跑得起来。CI 装的是那份文件，漏了 pymupdf 就让
这两个标签测试在收集阶段直接 ERROR（2026-10-22 就是这么红的）。
reportlab 生成的页面对象是明文，`/MediaBox` 和 `/Type /Page` 直接能扫出来，
够用且零依赖。
"""

import re

_MEDIABOX = re.compile(
    rb'/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]'
)
# /Type /Page 后面不能跟 s，否则把 /Pages 也数进去
_PAGE = re.compile(rb'/Type\s*/Page(?!s)')


def page_sizes(data):
    """按出现顺序返回 [(宽pt, 高pt), ...]，一页一项。"""
    return [
        (float(m.group(3)) - float(m.group(1)), float(m.group(4)) - float(m.group(2)))
        for m in _MEDIABOX.finditer(data)
    ]


def page_count(data):
    return len(_PAGE.findall(data))


def page_mm(data):
    """返回 (宽mm, 高mm, 页数)，四舍五入到 0.1mm。"""
    width, height = page_sizes(data)[0]
    return round(width / 72 * 25.4, 1), round(height / 72 * 25.4, 1), page_count(data)
