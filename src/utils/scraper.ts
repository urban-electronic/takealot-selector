/**
 * Browser-based Takealot scraper.
 * Uses WebView's native fetch() (WebKit engine) to pass Cloudflare's browser check,
 * then parses HTML with DOMParser.
 */

export interface ScrapeResult {
  normalized_url: string;
  tsin: string | null;
  product_name: string | null;
  product_image_url: string | null;
  actual_sale_price_zar: number | null;
  in_stock_price: number | null;
  takealot_category_path: string | null;
  recommended_fee_category: string | null;
  fee_category_confidence: 'high' | 'medium' | 'low';
  fee_match_reason: string | null;
  warnings: string[];
  success: boolean;
}

function normalizeUrl(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.origin}${parsed.pathname}`;
  } catch {
    return url;
  }
}

function parsePrice(text: string): number | null {
  const cleaned = text.replace(/\s/g, '');
  const match = cleaned.match(/[\d,]+\.?\d*/);
  if (match) {
    const val = parseFloat(match[0].replace(/,/g, ''));
    return isNaN(val) ? null : val;
  }
  return null;
}

const CATEGORY_KEYWORDS = [
  'computers', 'electronics', 'home', 'kitchen', 'sport',
  'fashion', 'baby', 'toys', 'garden', 'automotive',
  'camping', 'beauty', 'health', 'office', 'books',
  'appliances', 'gaming', 'music', 'pets', 'liquor',
  'stationery', 'luggage', 'data storage', 'drives',
];

const FEE_CATEGORY_RULES: Array<{ category: string; fee: string; reason: string }> = [
  { category: 'Computers', fee: 'Computers', reason: 'Takealot 分类含 Computers' },
  { category: 'electronics', fee: 'Electronics', reason: 'Takealot 分类含 Electronics' },
  { category: 'home', fee: 'Home & Kitchen', reason: 'Takealot 分类含 Home' },
  { category: 'kitchen', fee: 'Home & Kitchen', reason: 'Takealot 分类含 Kitchen' },
  { category: 'sport', fee: 'Sports & Outdoors', reason: 'Takealot 分类含 Sport' },
  { category: 'fashion', fee: 'Fashion', reason: 'Takealot 分类含 Fashion' },
  { category: 'baby', fee: 'Baby', reason: 'Takealot 分类含 Baby' },
  { category: 'toys', fee: 'Toys', reason: 'Takealot 分类含 Toys' },
  { category: 'garden', fee: 'Garden & Outdoor', reason: 'Takealot 分类含 Garden' },
  { category: 'automotive', fee: 'Automotive', reason: 'Takealot 分类含 Automotive' },
  { category: 'camping', fee: 'Camping & Outdoor', reason: 'Takealot 分类含 Camping' },
  { category: 'beauty', fee: 'Beauty', reason: 'Takealot 分类含 Beauty' },
  { category: 'health', fee: 'Health & Personal Care', reason: 'Takealot 分类含 Health' },
  { category: 'office', fee: 'Office', reason: 'Takealot 分类含 Office' },
  { category: 'books', fee: 'Books', reason: 'Takealot 分类含 Books' },
  { category: 'appliances', fee: 'Appliances', reason: 'Takealot 分类含 Appliances' },
  { category: 'gaming', fee: 'Gaming', reason: 'Takealot 分类含 Gaming' },
  { category: 'music', fee: 'Music', reason: 'Takealot 分类含 Music' },
  { category: 'pets', fee: 'Pet Supplies', reason: 'Takealot 分类含 Pets' },
  { category: 'liquor', fee: 'Liquor', reason: 'Takealot 分类含 Liquor' },
  { category: 'stationery', fee: 'Stationery', reason: 'Takealot 分类含 Stationery' },
  { category: 'luggage', fee: 'Luggage', reason: 'Takealot 分类含 Luggage' },
  { category: 'data storage', fee: 'Computers', reason: 'Takealot 分类含 Data Storage' },
  { category: 'drives', fee: 'Computers', reason: 'Takealot 分类含 Drives' },
];

function recommendFeeCategory(categoryPath: string | null): { name: string | null; reason: string | null; confidence: 'high' | 'medium' | 'low' } {
  if (!categoryPath) return { name: null, reason: null, confidence: 'low' };
  const lower = categoryPath.toLowerCase();
  for (const rule of FEE_CATEGORY_RULES) {
    if (lower.includes(rule.category)) {
      return { name: rule.fee, reason: rule.reason, confidence: 'high' };
    }
  }
  return { name: null, reason: null, confidence: 'medium' };
}

export async function scrapeTakealot(productUrl: string): Promise<ScrapeResult> {
  const normalized = normalizeUrl(productUrl);

  if (!normalized.includes('takealot.com')) {
    return {
      normalized_url: normalized,
      tsin: null, product_name: null, product_image_url: null,
      actual_sale_price_zar: null, in_stock_price: null,
      takealot_category_path: null,
      recommended_fee_category: null, fee_category_confidence: 'low', fee_match_reason: null,
      warnings: ['URL 不属于 takealot.com'],
      success: false,
    };
  }

  // Extract TSIN from URL
  const tsinMatch = productUrl.match(/PLID(\d+)/);
  const tsin = tsinMatch ? `PLID${tsinMatch[1]}` : null;

  const warnings: string[] = [];

  // Fetch page HTML via WebView's native fetch (WebKit engine, passes Cloudflare)
  let resp: Response;
  try {
    resp = await fetch(normalized, {
      headers: {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-ZA,en-US;q=0.9,en;q=0.8',
      },
    });
  } catch (e: any) {
    return {
      normalized_url: normalized,
      tsin, product_name: null, product_image_url: null,
      actual_sale_price_zar: null, in_stock_price: null,
      takealot_category_path: null,
      recommended_fee_category: null, fee_category_confidence: 'low', fee_match_reason: null,
      warnings: [`请求失败: ${e.message}`],
      success: false,
    };
  }

  if (resp.status >= 400) {
    return {
      normalized_url: normalized,
      tsin, product_name: null, product_image_url: null,
      actual_sale_price_zar: null, in_stock_price: null,
      takealot_category_path: null,
      recommended_fee_category: null, fee_category_confidence: 'low', fee_match_reason: null,
      warnings: [`HTTP ${resp.status}`],
      success: false,
    };
  }

  const html = await resp.text();

  // Check for Cloudflare challenge
  if (html.includes('Just a moment') || html.includes('Checking your browser') || html.includes('cf-browser-verification')) {
    warnings.push('Cloudflare 安全验证截拦，请稍后再试');
    return {
      normalized_url: normalized,
      tsin, product_name: null, product_image_url: null,
      actual_sale_price_zar: null, in_stock_price: null,
      takealot_category_path: null,
      recommended_fee_category: null, fee_category_confidence: 'low', fee_match_reason: null,
      warnings,
      success: false,
    };
  }

  // Parse HTML with DOMParser
  const parser = new DOMParser();
  const doc = parser.parseFromString(html, 'text/html');

  // Product name from <title>
  let productName: string | null = null;
  const titleEl = doc.querySelector('title');
  if (titleEl) {
    const titleText = titleEl.textContent || '';
    if (titleText.toLowerCase().includes('takealot')) {
      const parts = titleText.split('|');
      if (parts.length > 0) {
        const name = parts[0].trim();
        if (name) productName = name;
      }
    }
  }

  // Product image
  let productImageUrl: string | null = null;
  const imgEl = doc.querySelector('img[src*="media.takealot.com/covers_images"]');
  if (imgEl) {
    const src = imgEl.getAttribute('src');
    if (src) {
      productImageUrl = src.replace('s-thumbnail', 's-pdpxl');
    }
  }

  // Price - look for elements with price-buybox class
  let actualSalePrice: number | null = null;
  const priceEl = doc.querySelector('[class*="price-buybox"]');
  if (priceEl) {
    const priceText = priceEl.textContent || '';
    actualSalePrice = parsePrice(priceText);
  }

  // Category path from .pdp area
  let categoryPath: string | null = null;
  const pdpEl = doc.querySelector('.pdp');
  if (pdpEl) {
    const links = pdpEl.querySelectorAll('a[href]');
    const cats: string[] = [];
    const seen = new Set<string>();
    for (const link of links) {
      const text = (link.textContent || '').trim();
      if (!text || text.length > 50) continue;
      const lower = text.toLowerCase();
      if (CATEGORY_KEYWORDS.some(kw => lower.includes(kw)) && !seen.has(text)) {
        seen.add(text);
        cats.push(text);
      }
    }
    if (cats.length > 0) {
      categoryPath = cats.join(' > ');
    }
  }

  // Fee category recommendation
  const feeRec = recommendFeeCategory(categoryPath);

  return {
    normalized_url: normalized,
    tsin,
    product_name: productName,
    product_image_url: productImageUrl,
    actual_sale_price_zar: actualSalePrice,
    in_stock_price: actualSalePrice,
    takealot_category_path: categoryPath,
    recommended_fee_category: feeRec.name,
    fee_category_confidence: feeRec.confidence,
    fee_match_reason: feeRec.reason,
    warnings,
    success: true,
  };
}
