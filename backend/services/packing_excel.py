# -*- coding: utf-8 -*-
"""
云端装箱单 Excel 生成引擎
======================

完整移植本地版 kunjia_analyze/app.py 的 generate() 逻辑：
- 以 backend/packing_template.xlsx（即本地 template.xlsx 副本）为母版
- 删除第 4 行起的旧数据行
- 移除 K7 附近模板自带的固定浮动图（drawing1.xml 中 col=10 / row>=3 的 anchor）
- 按 template_row 深拷贝对应模板行样式
- A-M 列写入（A 唛头 / B SKU / C 名称 / D 箱数 / E 内件数量 / F 单位 / G 重量 /
  H 材质 / I 有牌子 / J 电池 / K DISPIMG 公式 / L 带电 / M 带磁）
- 内件数量进 E 列
- 产品图片写入 xl/media/local_<n>.<ext>，并在 WPS 私有 etc:cellImage +
  _xlfn.DISPIMG("ID...",1) 结构中建立引用
- 输出文件名由调用方决定（路由层命名为 packing_{date}.xlsx）

注意：模板是 WPS 私有扩展结构（etc:cellImage + _xlfn.DISPIMG），openpyxl 会
丢弃这些扩展，因此这里与本地版一致采用 zipfile + ElementTree 的 XML 级生成，
不依赖 openpyxl（Python 标准库即可）。
"""

import copy
import hashlib
import os
import re
import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 图片目录与数据库同持久化卷（Railway 挂载卷），保证重启不丢
DB_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", BASE_DIR)
IMAGE_DIR = Path(DB_DIR) / "packing_images"
TEMPLATE = Path(BASE_DIR) / "packing_template.xlsx"

MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
DRAW = 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
ETC = 'http://www.wps.cn/officeDocument/2017/etCustomData'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
Q = lambda n: '{%s}%s' % (MAIN, n)

for prefix, uri in [('', MAIN), ('xdr', DRAW), ('a', A), ('r', R), ('etc', ETC)]:
    ET.register_namespace(prefix, uri)


# ---- 文本推断工具（与本地 app.py 一致，用于前端未填写时的自动补全） ----

def split_name(value):
    parts = (value or '').split('/', 1)
    return parts[0].strip(), parts[1].strip() if len(parts) > 1 else ''


def infer_flags(name_zh, name_en, electric_hint=''):
    """保守的名称筛选；不确定项保持待确认，由人工复核。"""
    text = (name_zh + ' ' + name_en).lower()
    battery_yes = bool(re.search(
        r'电池|锂电|充电宝|充电式|可充电|蓄电|电动牙刷|无线耳机|蓝牙耳机|智能手表|手持风扇|'
        r'battery|rechargeable|power bank|cordless|wireless earbuds|smart watch', text))
    magnetic_yes = bool(re.search(
        r'磁铁|磁吸|磁性|硬盘|扬声器|音箱|喇叭|magnet|hard drive|hdd|speaker', text))
    passive = bool(re.search(
        r'袜|手套|手袋|背包|浴袍|毛巾|帽子|帽|鞋|衣|裤|围巾|文胸|眼罩|袜套|hair towel|'
        r'gloves|socks|bag|towel|robe|hat|shoes|clothing', text))
    electronic = bool(re.search(
        r'电子|电动|蓝牙|无线|usb|wifi|wi-fi|摄像|相机|屏幕|充电|led|智能|传感|适配器|录音|'
        r'开关|插头|灯具|手柄|无人机|camera|adapter|wireless|bluetooth|sensor|smart |electric|motor',
        text))
    electric = '是' if electric_hint == '是' or battery_yes else ('待确认' if electronic else '否')
    magnetic = '是' if magnetic_yes else ('待确认' if electronic and not passive else '否')
    return electric, magnetic


def infer_material(name_zh, name_en):
    text = (name_zh + ' ' + name_en).lower()
    if re.search(r'硅胶|silicone', text):
        return '硅胶'
    if re.search(r'玻璃|glass', text):
        return '玻璃'
    if re.search(r'皮革|真皮|leather', text):
        return '皮革'
    if re.search(r'帆布|canvas', text):
        return '帆布'
    if re.search(r'藤|草编|proofing basket|rattan|wicker', text):
        return '藤编'
    if re.search(r'陶瓷|ceramic', text):
        return '陶瓷'
    if re.search(r'木|wood|bamboo|竹', text):
        return '木质'
    if re.search(r'钢|铁|铝|合金|金属|不锈钢|stainless|steel|aluminum|metal', text):
        return '金属'
    if re.search(r'袜|手套|衣|裤|帽|鞋|浴袍|毛巾|布|围巾|背包|socks|gloves|towel|fabric|clothing|robe|hat|textile', text):
        return '化纤'
    if re.search(r'塑料|abs|pvc|塑胶|plastic|镜盖|适配器|adapter|usb|wifi|开关|switch|硬盘|hdd|玩具|toy|电子|电器', text):
        return '塑料'
    return '混合'


# ---- 图片校验 ----

def check_image(path):
    p = Path(path)
    if p.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('图片不能超过 20 MB')
    header = p.read_bytes()[:12]
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if header.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    raise ValueError('仅支持 PNG 或 JPG 图片')


def add_image(sku, path):
    """以内容 SHA256 哈希命名存放到 packing_images 目录（同名自动去重）。"""
    ext = check_image(path)
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    filename = digest + '.' + ext
    target = IMAGE_DIR / filename
    if not target.exists():
        shutil.copyfile(path, target)
    return filename


# ---- XML 级写入 ----

def setcell(row, col, val, numeric=False, formula=None):
    coord = col + row.attrib['r']
    c = next((x for x in row if x.tag == Q('c') and x.get('r') == coord), None)
    if c is None:
        c = ET.SubElement(row, Q('c'), {'r': coord})
    for child in list(c):
        c.remove(child)
    if formula:
        c.attrib.pop('t', None)
        ET.SubElement(c, Q('f')).text = formula
    elif numeric and val != '':
        c.attrib.pop('t', None)
        ET.SubElement(c, Q('v')).text = str(val)
    else:
        c.set('t', 'inlineStr')
        ET.SubElement(ET.SubElement(c, Q('is')), Q('t')).text = str(val if val is not None else '')


def generate(items, date, mark, shipping, address, output):
    """
    items: [(product_dict, cartons, count), ...]
    product_dict 需含 template_row / name_zh / name_en / sku / unit / weight /
    material / brand / battery / electric / magnetic / image_file
    """
    with zipfile.ZipFile(TEMPLATE) as src:
        root = ET.fromstring(src.read('xl/worksheets/sheet1.xml'))
        data = root.find(Q('sheetData'))
        rows = {int(r.get('r')): r for r in data.findall(Q('row'))}
        # 模板第 4-8 行存在 K 列公式（含示例 DISPIMG），按 template_row 复用
        formulas = {}
        for n in range(4, 9):
            c = next((c for c in rows[n] if c.get('r') == 'K' + str(n)), None)
            f = c.find(Q('f')) if c is not None else None
            if f is not None:
                formulas[n] = f.text
        # 删除第 4 行起的旧数据行
        for n in sorted(rows):
            if n >= 4:
                data.remove(rows[n])
        image_xml = ET.fromstring(src.read('xl/cellimages.xml'))
        drawing_xml = ET.fromstring(src.read('xl/drawings/drawing1.xml'))
        # 移除模板自带的 K7 固定浮动图（避免输出产品重排后错位）
        for anchor in list(drawing_xml):
            origin = anchor.find('{%s}from' % DRAW)
            if origin is None or anchor.find('{%s}pic' % DRAW) is None:
                continue
            col = origin.find('{%s}col' % DRAW)
            rownum = origin.find('{%s}row' % DRAW)
            if col is not None and rownum is not None and int(col.text) == 10 and int(rownum.text) >= 3:
                drawing_xml.remove(anchor)
        image_rels = ET.fromstring(src.read('xl/_rels/cellimages.xml.rels'))
        content_types = ET.fromstring(src.read('[Content_Types].xml'))
        # 找到模板中的示例 cellImage pic 作为深拷贝母版
        picture = None
        for candidate in image_xml.findall('{%s}cellImage/{%s}pic' % (ETC, DRAW)):
            marker = candidate.find('.//{%s}cNvPr' % DRAW)
            if marker is not None and marker.get('name') == 'ID_64762FB14869452EA635507BAC1CB319':
                picture = candidate
                break
        if picture is None:
            pics = image_xml.findall('{%s}cellImage/{%s}pic' % (ETC, DRAW))
            if pics:
                picture = pics[0]
        if picture is None:
            raise ValueError('模板缺少 cellImage pic 母版，无法生成带图片的装箱单')
        next_rel = max(int(x.get('Id')[3:]) for x in image_rels) + 1
        extras = {}
        for i, (p, cartons, count) in enumerate(items):
            rn = i + 4
            row = copy.deepcopy(rows.get(p['template_row'], rows[8]))
            row.set('r', str(rn))
            for c in row.findall(Q('c')):
                c.set('r', re.sub(r'\d+$', str(rn), c.get('r')))
            name = ' / '.join(x for x in [p['name_zh'], p['name_en']] if x)
            values = {
                'A': mark if i == 0 else '',
                'B': p['sku'],
                'C': name,
                'D': cartons,
                'E': count,
                'F': p['unit'],
                'G': p['weight'],
                'H': p['material'],
                'I': p['brand'],
                'J': p['battery'],
                'L': p['electric'],
                'M': p['magnetic'],
            }
            for col, val in values.items():
                setcell(row, col, val, col in ('D', 'E', 'G') and str(val).replace('.', '', 1).isdigit())
            cname = next(c for c in row if c.tag == Q('c') and c.get('r') == 'C' + str(rn))
            cname.set('s', next(c.get('s') for c in rows[4] if c.get('r') == 'C4'))
            formula = formulas.get(p['template_row'])
            filename = p['image_file'] or ''
            if filename:
                if Path(filename).name != filename:
                    raise ValueError('图片文件名无效')
                local = IMAGE_DIR / filename
                if not local.is_file():
                    raise FileNotFoundError('产品 ' + p['sku'] + ' 的图片丢失，请重新上传')
                ext = check_image(local)
                relid = 'rId' + str(next_rel)
                next_rel += 1
                imageid = 'ID_LOCAL_' + hashlib.sha256((p['sku'] + str(rn)).encode()).hexdigest()[:24].upper()
                media = 'xl/media/local_' + str(rn) + '.' + ext
                extras[media] = local.read_bytes()
                ET.SubElement(image_rels, '{%s}Relationship' % REL, {
                    'Id': relid,
                    'Type': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/image',
                    'Target': 'media/local_' + str(rn) + '.' + ext,
                })
                ci = ET.SubElement(image_xml, '{%s}cellImage' % ETC)
                pic = copy.deepcopy(picture)
                pic.find('.//{%s}cNvPr' % DRAW).set('name', imageid)
                pic.find('.//{%s}cNvPr' % DRAW).set('id', str(1000 + rn))
                blip = pic.find('.//{%s}blip' % A)
                blip.set('{%s}embed' % R, relid)
                blip.attrib.pop('{%s}link' % R, None)
                ci.append(pic)
                if ext == 'jpg' and not any(x.get('Extension') == 'jpg' for x in content_types):
                    ET.SubElement(content_types, '{%s}Default' % CT, {
                        'Extension': 'jpg',
                        'ContentType': 'image/jpeg',
                    })
                formula = '_xlfn.DISPIMG("' + imageid + '",1)'
            setcell(row, 'K', '', formula=formula)
            data.insert(min(3 + i, len(data)), row)
        setcell(rows[2], 'B', mark)
        setcell(rows[2], 'G', shipping)
        setcell(rows[2], 'J', address)
        dim = root.find(Q('dimension'))
        if dim is not None:
            dim.set('ref', 'A1:O' + str(max(10, len(items) + 3)))
        changed = {
            'xl/worksheets/sheet1.xml': root,
            'xl/drawings/drawing1.xml': drawing_xml,
        }
        if extras:
            changed.update({
                'xl/cellimages.xml': image_xml,
                'xl/_rels/cellimages.xml.rels': image_rels,
                '[Content_Types].xml': content_types,
            })
        with zipfile.ZipFile(output, 'w') as dst:
            for entry in src.infolist():
                xml = changed.get(entry.filename)
                dst.writestr(entry, ET.tostring(xml, encoding='utf-8', xml_declaration=True) if xml is not None else src.read(entry.filename))
            for path, content in extras.items():
                dst.writestr(path, content, compress_type=zipfile.ZIP_DEFLATED)
