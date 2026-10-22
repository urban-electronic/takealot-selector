/**
 * 客户端版 Takealot Product Label —— 一个 SKU 一个 40x30mm PDF。
 *
 * 版式和 backend/services/label_pdf.py 完全一致（两边都是从官方 product_labels.pdf
 * 量出来的同一组参数）：Helvetica 8pt，产品名 3 行在上，EAN-13 横排、
 * 最小条宽 0.33mm、数据条高 7.61mm、起/中/止六根护线 31.181pt（伸到数字那一行），
 * 13 位数字按 1-6-6 分组压在条码下，条码左侧固定 M / P 两个标记。
 * 整块内容 34.3 x 21.8mm。
 *
 * 「跟官方裁剪后一模一样」是逐条比对过的：同 SKU 下 17 段文字、30 个条码矩形
 * x/y/宽/高全部一致（残差 ≤0.04pt 是官方自己写 PDF 时的舍入），
 * 203 / 300 / 400 / 600 / 1200 dpi 全部扫得出。
 *
 * 为什么前端自己出 PDF：后端部署（Railway）不可控的时候，标签这种「打印前必须
 * 拿到手」的东西不该卡在部署上。这里零依赖手写 PDF + ZIP，打开页面就能下。
 *
 * 注意：本文件是被 ProductList 直接 import 的叶子模块，除 react 外不要 import
 * 任何相对路径（jsdom 测试的打桩只作用在被编译的那一层）。
 */

const MM = 72 / 25.4; // 1mm = 2.8346pt

// --- 官方 product_labels.pdf 量出来的版式，单位 pt，原点在内容左下角 ---
// 数值与 backend/services/label_pdf.py 一字不差，改一边必须同步另一边。
const CONTENT_W = 97.24;
const CONTENT_H = 61.9;
const NAME_SIZE = 8.0;
const NAME_BASELINES = [53.3, 45.3, 37.3];
// 折行宽度比内容宽多 0.5pt 容差：官方第 2 行实测 97.35pt，比内容宽还多 0.11 也照样
// 排在同一行；用 CONTENT_W - BAR_X（88.82）会提前折行，三行断词位置就跟官方不一样。
const NAME_MAX_WIDTH = CONTENT_W + 0.5;
const DIGITS_BASELINE = 2.37;
const BAR_X = 8.42; // 官方首条（起始护线第一根）的 x
const BAR_TOP = 31.64; // 条码顶部，数据条和护线顶对齐
const BAR_WIDTH = 0.935; // pt = 0.33mm，95 个模块正好铺满 88.825pt
const BAR_HEIGHT = 21.581; // 数据条高（7.61mm）
const GUARD_HEIGHT = 31.181; // 护线高：比数据条长 9.6pt，伸到 13 位数字那一行
const MARK_X = 0.0;
const MARK_M_BASELINE = 23.13;
const MARK_P_BASELINE = 14.63;
const DIGIT_X = [0.93, 17.52, 61.49];
const PAD = 1.6 * MM;

// EAN-13 的 95 个模块里哪些是护线（起始 101 / 中间 01010 / 终止 101）。
// 护线必须画得比数据条长 —— 这是官方标签最容易被漏掉的一处细节。
const GUARD_MODULES = new Set([0, 2, 46, 48, 92, 94]);

export type LabelSize = '40x30' | '30x40';

export const LABEL_SIZES: Record<LabelSize, [number, number]> = {
  '40x30': [40, 30],
  '30x40': [30, 40],
};

export const DEFAULT_LABEL_SIZE: LabelSize = '40x30';

export interface LabelItem {
  sku: string;
  name: string;
  /** 中文简写，只进文件名（SKU 是 13 位数字，光看分不出是哪个品） */
  cn?: string;
  copies?: number;
}

export interface LabelBundle {
  blob: Blob;
  /** 总张数（含 copies） */
  count: number;
  /** 出不了标签的 SKU 及原因 */
  skipped: { sku: string; reason: string }[];
  /** 单 SKU 出的是 pdf，多 SKU 出的是 zip */
  kind: 'pdf' | 'zip';
  filename: string;
}

// --- Helvetica 标准 AFM 字宽（ASCII 32..126，千分之一 em），用于折行 ---
const HELV_W = [
  278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
  556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
  1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
  667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
  333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
  556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
];

function textWidth(text: string, size: number): number {
  let total = 0;
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    total += code >= 32 && code <= 126 ? HELV_W[code - 32] : 556;
  }
  return (total / 1000) * size;
}

/**
 * 只保留 ASCII：Helvetica 能画，而且字节数 == 字符数，
 * PDF 的 /Length 和 xref 偏移才算得准（含多字节字符会把整份文件写坏）。
 */
function asciiOnly(text: string): string {
  let out = '';
  for (const ch of text) {
    const code = ch.charCodeAt(0);
    out += code >= 32 && code <= 126 ? ch : '?';
  }
  return out;
}

function wrapName(name: string, size: number, maxWidth: number, maxLines: number): string[] {
  // 只有真的把词丢掉时才补省略号。原来的判据是「凑满 maxLines 就截末行加 ...」，
  // 于是「正好折成 3 行、第三行又刚好填满」的产品名会被截掉最后一个词——名字本来
  // 完整，印出来却带省略号。40×30 的标签内容宽只有 34.3mm、8pt，长名字很容易占满 3 行。
  const words = asciiOnly(name).split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let cur = '';
  let dropped = false;
  for (const word of words) {
    const trial = cur ? `${cur} ${word}` : word;
    if (textWidth(trial, size) <= maxWidth) {
      cur = trial;
      continue;
    }
    if (cur) lines.push(cur);
    cur = '';
    if (lines.length === maxLines) {
      dropped = true;
      break;
    }
    cur = word;
  }
  if (cur && lines.length < maxLines) lines.push(cur);
  if (dropped && lines.length) {
    let last = lines[lines.length - 1];
    while (textWidth(`${last}...`, size) > maxWidth && last.length > 1) {
      last = last.slice(0, -1);
    }
    lines[lines.length - 1] = `${last}...`;
  }
  return lines;
}

// --- EAN-13 ---

const EAN_L = ['0001101', '0011001', '0010011', '0111101', '0100011', '0110001', '0101111', '0111011', '0110111', '0001011'];
const EAN_G = ['0100111', '0110011', '0011011', '0100001', '0011101', '0111001', '0000101', '0010001', '0001001', '0010111'];
const EAN_R = ['1110010', '1100110', '1101100', '1000010', '1011100', '1001110', '1010000', '1000100', '1001000', '1110100'];
const EAN_PARITY = ['LLLLLL', 'LLGLGG', 'LLGGLG', 'LLGGGL', 'LGLLGG', 'LGGLLG', 'LGGGLL', 'LGLGLG', 'LGLGGL', 'LGGLGL'];

/** 13 位数字且校验位正确，才画得出可扫的 EAN-13 */
export function isEan13(code: string | null | undefined): boolean {
  if (!code || code.length !== 13 || !/^\d{13}$/.test(code)) return false;
  let total = 0;
  for (let i = 0; i < 12; i += 1) total += Number(code[i]) * (i % 2 ? 3 : 1);
  return (10 - (total % 10)) % 10 === Number(code[12]);
}

/** 返回 95 个模块（1 = 黑条），即 EAN-13 的标准结构 */
export function ean13Modules(code: string): number[] {
  const parity = EAN_PARITY[Number(code[0])];
  let bits = '101';
  for (let i = 0; i < 6; i += 1) {
    const digit = Number(code[i + 1]);
    bits += parity[i] === 'L' ? EAN_L[digit] : EAN_G[digit];
  }
  bits += '01010';
  for (let i = 0; i < 6; i += 1) bits += EAN_R[Number(code[i + 7])];
  bits += '101';
  return bits.split('').map((b) => (b === '1' ? 1 : 0));
}

// --- 极简 PDF ---

function pdfStr(text: string): string {
  return text.replace(/\\/g, '\\\\').replace(/\(/g, '\\(').replace(/\)/g, '\\)');
}

function contentStream(name: string, code: string, labelW: number, labelH: number): string {
  const scale = Math.min(
    (labelW - 2 * PAD) / CONTENT_W,
    (labelH - 2 * PAD) / CONTENT_H,
    1.0,
  );
  const contentW = CONTENT_W * scale;
  const contentH = CONTENT_H * scale;
  const ox = (labelW - contentW) / 2;
  const oy = (labelH - contentH) / 2;

  // 整块内容按官方比例等比缩放后居中（40x30 下 scale 正好是 1，1:1 复刻）
  const ops: string[] = [
    'q',
    '0 g',
    `${scale.toFixed(4)} 0 0 ${scale.toFixed(4)} ${ox.toFixed(4)} ${oy.toFixed(4)} cm`,
  ];

  // 1) 产品名 3 行
  const lines = wrapName(name, NAME_SIZE, NAME_MAX_WIDTH, NAME_BASELINES.length);
  ops.push('BT', `/F1 ${NAME_SIZE} Tf`);
  lines.forEach((line, i) => {
    ops.push(`1 0 0 1 0 ${NAME_BASELINES[i].toFixed(2)} Tm`, `(${pdfStr(line)}) Tj`);
  });
  ops.push('ET');

  // 2) 条码：连续黑模块合并成矩形，但**护线和数据条高度不同，不能合并**。
  //    护线（起/中/止六根）比数据条长 9.6pt，一直伸到 13 位数字那一行；
  //    少了它标签会比官方矮一截。
  const modules = ean13Modules(code);
  let i = 0;
  while (i < modules.length) {
    if (!modules[i]) {
      i += 1;
      continue;
    }
    const guard = GUARD_MODULES.has(i);
    let run = 1;
    while (
      i + run < modules.length
      && modules[i + run]
      && GUARD_MODULES.has(i + run) === guard
    ) run += 1;
    const h = guard ? GUARD_HEIGHT : BAR_HEIGHT;
    const x = BAR_X + i * BAR_WIDTH;
    ops.push(`${x.toFixed(3)} ${(BAR_TOP - h).toFixed(3)} ${(run * BAR_WIDTH).toFixed(3)} ${h.toFixed(3)} re f`);
    i += run;
  }

  // 3) 13 位数字 1-6-6 分组
  ops.push('BT', `/F1 ${NAME_SIZE} Tf`);
  [code[0], code.slice(1, 7), code.slice(7, 13)].forEach((chunk, i2) => {
    ops.push(`1 0 0 1 ${DIGIT_X[i2].toFixed(2)} ${DIGITS_BASELINE.toFixed(2)} Tm`, `(${pdfStr(chunk)}) Tj`);
  });
  // 4) 条码左侧 M / P
  ops.push(`1 0 0 1 ${MARK_X.toFixed(2)} ${MARK_M_BASELINE.toFixed(2)} Tm`, '(M) Tj');
  ops.push(`1 0 0 1 ${MARK_X.toFixed(2)} ${MARK_P_BASELINE.toFixed(2)} Tm`, '(P) Tj');
  ops.push('ET', 'Q');
  return ops.join('\n');
}

function buildPdf(pages: { stream: string }[], wPt: number, hPt: number, title: string): Uint8Array<ArrayBuffer> {
  const chunks: string[] = [];
  let offset = 0;
  const push = (s: string) => {
    chunks.push(s);
    offset += s.length; // 内容全是 ASCII，字符数 == 字节数
  };
  const offsets: number[] = [];
  const addObj = (num: number, body: string) => {
    offsets[num] = offset;
    push(`${num} 0 obj\n${body}\nendobj\n`);
  };

  push('%PDF-1.4\n');
  const n = pages.length;
  const pageNums = pages.map((_, i) => 4 + i * 2);
  const fontNum = 3;
  const total = 3 + n * 2 + 1;

  addObj(1, '<< /Type /Catalog /Pages 2 0 R >>');
  addObj(2, `<< /Type /Pages /Kids [${pageNums.map((p) => `${p} 0 R`).join(' ')}] /Count ${n} >>`);
  addObj(fontNum, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>');

  pages.forEach((page, i) => {
    const pageNum = pageNums[i];
    const contentNum = pageNum + 1;
    addObj(
      pageNum,
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${wPt.toFixed(3)} ${hPt.toFixed(3)}] `
        + `/Resources << /Font << /F1 ${fontNum} 0 R >> >> /Contents ${contentNum} 0 R >>`,
    );
    addObj(contentNum, `<< /Length ${page.stream.length} >>\nstream\n${page.stream}\nendstream`);
  });

  const infoNum = total;
  addObj(infoNum, `<< /Title (${pdfStr(title)}) /Producer (takealot-selector) >>`);

  const xrefStart = offset;
  push(`xref\n0 ${total + 1}\n`);
  push('0000000000 65535 f \n');
  for (let i = 1; i <= total; i += 1) {
    push(`${String(offsets[i] || 0).padStart(10, '0')} 00000 n \n`);
  }
  push(`trailer\n<< /Size ${total + 1} /Root 1 0 R /Info ${infoNum} 0 R >>\nstartxref\n${xrefStart}\n%%EOF\n`);

  const bytes = new Uint8Array(offset);
  const enc = new TextEncoder();
  let pos = 0;
  for (const chunk of chunks) {
    const buf = enc.encode(chunk);
    bytes.set(buf, pos);
    pos += buf.length;
  }
  return bytes.subarray(0, pos);
}

function safeFilename(text: string): string {
  return (text || '').replace(/[\\/:*?"<>|\s]+/g, '_').trim() || 'label';
}

/** 规格名占位值（没对上真实颜色/尺码时会出现），不能拿来当文件名 */
const PLACEHOLDER_LABEL = /^(主商品|规格待识别|规格待确认|历史分支)$/;

/**
 * 文件名里的中文简写：SKU 之外的第二个识别依据。
 * 打印软件和资源管理器里光看 13 位数字分不出是哪个品，加上中文名一眼就能挑。
 */
function shortName(cn: string | undefined): string {
  const cleaned = (cn || '')
    .replace(/[\\/:*?"<>|\r\n\t]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!cleaned || PLACEHOLDER_LABEL.test(cleaned)) return '';
  return cleaned.replace(/ /g, '_').slice(0, 20);
}

/** 一个 SKU 一个文件：`9902348623136_皮革黑色包.pdf` */
function labelFileName(sku: string, cn?: string): string {
  const short = shortName(cn);
  return short ? `${safeFilename(sku)}_${short}.pdf` : `${safeFilename(sku)}.pdf`;
}

/** 单个 SKU 的标签 PDF（copies > 1 时同一份里重复多页，连打就是多张） */
export function buildLabelPdf(name: string, sku: string, opts: { size?: LabelSize; copies?: number } = {}): Uint8Array<ArrayBuffer> {
  const [wmm, hmm] = LABEL_SIZES[opts.size || DEFAULT_LABEL_SIZE];
  const wPt = wmm * MM;
  const hPt = hmm * MM;
  const copies = Math.max(1, Math.min(Math.trunc(opts.copies || 1), 999));
  const stream = contentStream(name || sku, sku, wPt, hPt);
  const pages = Array.from({ length: copies }, () => ({ stream }));
  return buildPdf(pages, wPt, hPt, sku);
}

// --- 极简 ZIP（store 模式，不压缩；PDF 内部已经是压缩过的） ---

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i += 1) {
    let c = i;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[i] = c >>> 0;
  }
  return table;
})();

function crc32(data: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < data.length; i += 1) c = CRC_TABLE[(c ^ data[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

interface ZipEntry {
  name: string;
  data: Uint8Array;
}

function buildZip(entries: ZipEntry[]): Uint8Array<ArrayBuffer> {
  const enc = new TextEncoder();
  const parts: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  // 压缩包里的文件时间戳对标签没有意义，固定成 1980-01-01 00:00：
  // 一是不用碰本地时区（项目规定业务日期只能来自 utils/datetime），
  // 二是同一批 SKU 每次生成的字节完全一致，方便比对。
  const dosTime = 0;
  const dosDate = (1 << 5) | 1; // 月 = 1, 日 = 1, 年 = 1980

  const num = (v: number, bytes: number) => {
    const out = new Uint8Array(bytes);
    for (let i = 0; i < bytes; i += 1) out[i] = (v >>> (8 * i)) & 0xff;
    return out;
  };

  entries.forEach((entry) => {
    const nameBytes = enc.encode(entry.name);
    const crc = crc32(entry.data);
    const size = entry.data.length;

    const local = [
      num(0x04034b50, 4), num(20, 2), num(0x0800, 2), num(0, 2),
      num(dosTime, 2), num(dosDate, 2), num(crc, 4), num(size, 4), num(size, 4),
      num(nameBytes.length, 2), num(0, 2),
    ];
    local.forEach((p) => parts.push(p));
    parts.push(nameBytes, entry.data);

    const cd = [
      num(0x02014b50, 4), num(20, 2), num(20, 2), num(0x0800, 2), num(0, 2),
      num(dosTime, 2), num(dosDate, 2), num(crc, 4), num(size, 4), num(size, 4),
      num(nameBytes.length, 2), num(0, 2), num(0, 2), num(0, 2), num(0, 2),
      num(0, 4), num(offset, 4),
    ];
    cd.forEach((p) => central.push(p));
    central.push(nameBytes);

    offset += local.reduce((s, p) => s + p.length, 0) + nameBytes.length + size;
  });

  const cdStart = offset;
  let cdSize = 0;
  central.forEach((p) => {
    cdSize += p.length;
  });
  const eocd = [
    num(0x06054b50, 4), num(0, 2), num(0, 2),
    num(entries.length, 2), num(entries.length, 2),
    num(cdSize, 4), num(cdStart, 4), num(0, 2),
  ];

  const all = [...parts, ...central, ...eocd];
  const totalLen = all.reduce((s, p) => s + p.length, 0);
  const out = new Uint8Array(totalLen);
  let pos = 0;
  all.forEach((p) => {
    out.set(p, pos);
    pos += p.length;
  });
  return out;
}

/** 多 SKU → 一个 zip；只有一个 SKU → 直接给 pdf */
export function buildLabelBundle(items: LabelItem[], opts: { size?: LabelSize; copies?: number } = {}): LabelBundle {
  const usable: { sku: string; name: string; cn: string }[] = [];
  const skipped: { sku: string; reason: string }[] = [];
  for (const item of items) {
    const sku = (item.sku || '').trim();
    if (!sku) {
      skipped.push({ sku: '', reason: '没有条码' });
    } else if (!isEan13(sku)) {
      skipped.push({ sku, reason: '不是合法 EAN-13' });
    } else {
      usable.push({ sku, name: (item.name || '').trim() || sku, cn: item.cn || '' });
    }
  }

  const copies = Math.max(1, Math.min(Math.trunc(opts.copies || 1), 999));
  if (!usable.length) {
    return { blob: new Blob([], { type: 'application/pdf' }), count: 0, skipped, kind: 'pdf', filename: '' };
  }

  if (usable.length === 1) {
    const pdf = buildLabelPdf(usable[0].name, usable[0].sku, { size: opts.size, copies });
    return {
      blob: new Blob([pdf], { type: 'application/pdf' }),
      count: copies,
      skipped,
      kind: 'pdf',
      filename: labelFileName(usable[0].sku, usable[0].cn),
    };
  }

  const entries: ZipEntry[] = usable.map((u) => ({
    name: labelFileName(u.sku, u.cn),
    data: buildLabelPdf(u.name, u.sku, { size: opts.size, copies }),
  }));
  return {
    blob: new Blob([buildZip(entries)], { type: 'application/zip' }),
    count: usable.length * copies,
    skipped,
    kind: 'zip',
    filename: `product_labels_${usable.length}_skus.zip`,
  };
}

/** 触发浏览器下载 */
export function saveLabelBundle(bundle: LabelBundle): void {
  if (!bundle.filename) return;
  const url = URL.createObjectURL(bundle.blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = bundle.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}
