/** Display-only summaries. Stored procurement titles remain unchanged. */
const categories: [RegExp, string, string][] = [
  [/periodic table/i, 'Element table', '元素周期表'],
  [/(?:mouse|rodent).*trap|rodent zapper/i, 'Mouse trap', '捕鼠器'],
  [/lingerie.*dress|dress.*lingerie/i, 'Lingerie dress', '情趣连衣裙'],
  [/stud earrings/i, 'Stud earrings', '耳钉'], [/earrings/i, 'Earrings', '耳饰'],
  [/couples?.*necklace|necklace.*pair/i, 'Couple necklace', '情侣项链'], [/necklace/i, 'Necklace', '项链'],
  [/usb.*hub/i, 'USB hub', 'USB扩展坞'], [/voice recorder/i, 'Voice recorder', '录音笔'],
  [/bead.*(?:kit|diy)/i, 'Bead DIY kit', '串珠手工套装'], [/smart.*switch|switch.*smart/i, 'Smart switch', '智能开关'],
  [/vacuum.*bag|dust bag/i, 'Vacuum dust bags', '吸尘器尘袋'],
  [/mirror.*cover/i, 'Mirror covers', '后视镜外壳'], [/laptop.*(?:bag|briefcase)/i, 'Laptop bag', '电脑包'],
  [/brace/i, 'Support brace', '支撑护具'], [/camera/i, 'Camera', '摄像头'],
  [/charger/i, 'Charger', '充电器'], [/headphone|earphone|earbud/i, 'Headphones', '耳机'],
  [/speaker/i, 'Speaker', '音箱'], [/keyboard/i, 'Keyboard', '键盘'],
  [/backpack/i, 'Backpack', '背包'], [/watch/i, 'Watch', '手表'],
  [/light|lamp/i, 'Light', '灯具'], [/adapter/i, 'Adapter', '转换器'],
  [/organizer/i, 'Organizer', '收纳用品'], [/bottle/i, 'Bottle', '水杯'],
  [/toy/i, 'Toy', '玩具'], [/tool/i, 'Tool', '工具'],
];
const colors: [RegExp, string, string][] = [
  [/\bred\b/i, 'Red', '红'], [/\bblack\b/i, 'Black', '黑'], [/\bwhite\b/i, 'White', '白'],
  [/\bsilver\b/i, 'Silver', '银'], [/\bgold\b/i, 'Gold', '金'], [/\bblue\b/i, 'Blue', '蓝'],
  [/\bpink\b/i, 'Pink', '粉'], [/\bgreen\b/i, 'Green', '绿'], [/\bgrey\b|\bgray\b/i, 'Grey', '灰'],
];
export function productLabels(title: string | null, chineseName?: string | null) {
  const text = (title || '').replace(/\s+/g, ' ').trim();
  const category = categories.find(([pattern]) => pattern.test(text));
  const englishSpecs: string[] = []; const chineseSpecs: string[] = [];
  const capacity = text.match(/\b\d+(?:\.\d+)?\s*(?:GB|TB|G|W)\b/i);
  if (capacity) { englishSpecs.push(capacity[0].toUpperCase()); chineseSpecs.push(capacity[0].toUpperCase()); }
  const size = text.match(/(?:^|[\s-])(XXXL|XXL|2XL|3XL|XL|XS|S|M|L)(?=$|[\s,])/i);
  if (size) { englishSpecs.push(size[1].toUpperCase()); chineseSpecs.push(size[1].toUpperCase() + '码'); }
  const matchedColors = colors.filter(([pattern]) => pattern.test(text));
  if (matchedColors.length <= 2) for (const [, en, zh] of matchedColors) { englishSpecs.push(en); chineseSpecs.push(zh + '色'); }
  for (const [pattern, feature] of [[/\bmagnetic\b/i, '磁吸'], [/\bwireless\b/i, '无线'], [/noise (?:reduction|cancell)/i, '降噪'], [/high.voltage/i, '高压'], [/\bHDMI\b/i, 'HDMI'], [/USB[ -]?C|Type[ -]?C/i, 'USB-C'], [/zapper|(?:electric|electronic).*?(?:mouse|rodent).*?trap/i, '电击'], [/\b100W\b/i, '100W'], [/real elements?/i, '实物元素']] as [RegExp, string][]) {
    if (pattern.test(text) && !chineseSpecs.includes(feature)) chineseSpecs.push(feature);
  }
  const elements = text.match(/(\d+)\s+(?:real\s+)?elements?/i);
  if (elements) {
    const generic = chineseSpecs.indexOf('实物元素');
    if (generic >= 0) chineseSpecs.splice(generic, 1);
    englishSpecs.push(elements[1] + ' elements');
    chineseSpecs.push(elements[1] + (/real/i.test(elements[0]) ? '种实物元素' : '种元素'));
  }
  const count = text.match(/(?:set of|pack of)\s*(\d+)|(\d+)\s*[- ]?(?:pack|pcs|pieces|PK)\b/i);
  if (count) { const n = count[1] || count[2]; englishSpecs.push(n + ' pcs'); chineseSpecs.push(n + '件装'); }
  const manual = (chineseName || '').trim().split(/\n/)[0];
  const usefulManual = manual && /[\u3400-\u9fff]/.test(manual) && !/^(?:黑|白|红|蓝|绿|黄|金|银|粉|灰|棕|深灰|深蓝)色(?:[·\s].*)?$/.test(manual);
  const chinese = (usefulManual ? manual : category?.[2] || '未填写中文品名').slice(0, 24);
  const features = chineseSpecs.filter(feature => !chinese.includes(feature)).slice(0, 3);
  const core = category?.[1] || text.replace(/\b(?:Zenty|Elegant|Gift|for Women Girls|Hypoallergenic)\b/gi, '').replace(/\s+/g, ' ').trim();
  const english = [core, ...englishSpecs.slice(0, 3)].join(' · ');
  return { english: english.length > 62 ? english.slice(0, 59).trimEnd() + '…' : english || 'Unnamed product', chinese, features: features.join(' · ') };
}
