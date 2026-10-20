// Adapted from PricePilot native source 2026-10-12; see docs/pricepilot-integration.md.
const STOPWORDS = new Set<string>([
  "a", "an", "the", "and", "or", "for", "with", "without", "of", "in", "on", "to", "by", "from",
  "at", "as", "is", "are", "be", "it", "its", "this", "that", "these", "those", "your", "you",
  "not", "no", "yes", "all", "any", "each", "per", "into", "out", "up", "down", "over", "under",
  "new", "free", "delivery", "shipping", "ship", "sale", "best", "top", "hot", "latest", "deal",
  "quality", "premium", "original", "genuine", "official", "authentic", "style", "fashion",
  "gift", "gifts", "ideal", "perfect", "great", "good", "high", "low", "pro", "plus", "max",
  "super", "ultra", "smart", "multi", "multi-purpose", "universal", "portable", "durable",
  "set", "sets", "pack", "packs", "piece", "pieces", "pcs", "pc", "unit", "units", "kit", "kits",
  "combo", "bundle", "bundles", "lot", "pair", "pairs", "box", "boxes", "case", "cases",
  "size", "sizes", "color", "colors", "colour", "colours", "assorted", "random", "style",
  "small", "medium", "large", "xl", "xxl", "xxxl", "mini", "big", "extra",
  "men", "mens", "women", "womens", "kid", "kids", "boy", "boys", "girl", "girls", "adult",
  "adults", "unisex", "baby", "toddler", "child", "children",
  "black", "white", "red", "blue", "green", "grey", "gray", "silver", "gold", "golden", "pink",
  "purple", "brown", "beige", "yellow", "orange", "clear", "transparent", "multicolor",
  "multicolour", "rose", "navy", "teal", "ivory", "cream",
  "cm", "mm", "m", "meter", "metre", "inch", "inches", "ft", "foot", "feet", "kg", "g", "gram",
  "grams", "ml", "l", "litre", "liter", "litres", "liters", "oz", "lb", "watt", "watts", "w",
  "v", "volt", "volts", "hz", "mah", "gb", "tb", "mb", "mp", "amp", "amps", "ah", "rpm", "psi",
  "x", "xx", "xxx", "s", "xs", "l", "ll", "p", "t"
]);

export function normalizeTitle(s: string): string {
  return (s ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

function isNoise(token: string): boolean {
  if (token.length < 2) return true;
  if (STOPWORDS.has(token)) return true;
  if (/^\d+$/.test(token)) return true;
  return false;
}

export function tokenize(title: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const t of normalizeTitle(title).split(" ")) {
    if (!t || isNoise(t) || seen.has(t)) continue;
    seen.add(t);
    out.push(t);
  }
  return out;
}

export function extractBrand(title: string): string | null {
  const raw = (title ?? "").trim().split(/\s+/);
  if (raw.length === 0) return null;
  const first = raw[0].replace(/[^A-Za-z0-9-]/g, "");
  if (!first) return null;
  const looksLikeBrand = /^[A-Z]/.test(first) && !isNoise(first.toLowerCase());
  return looksLikeBrand ? first.toLowerCase() : null;
}
