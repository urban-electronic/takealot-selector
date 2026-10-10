import type { ScrapeResult } from './types';

export function captureTakealotPage(doc: Document, pageUrl: string) {
  const url = new URL(pageUrl);
  if (!['takealot.com', 'www.takealot.com'].includes(url.hostname) || !/^https?:$/.test(url.protocol) || url.username || url.password || !/PLID\d+/i.test(url.pathname)) throw new Error('请在正常打开的 Takealot 商品详情页点击此书签。');
  const main = doc.querySelector('main');
  const title = main?.querySelector('h1')?.textContent?.trim() || '';
  if (!main || !title || /verify|just a moment|access denied/i.test(title)) throw new Error('商品尚未显示，请等待商品标题和图片加载完成。');
  const image = Array.from(main.querySelectorAll('img')).find(img => img.getAttribute('alt') === title && (img.getAttribute('src') || '').includes('media.takealot.com/covers_images/'));
  const priceText = main.querySelector('[data-ref="price"] .currency, [class*="price-buybox"] .currency')?.textContent || '';
  const price = priceText.match(/R\s*([\d,]+(?:\.\d+)?)/);
  const categories = Array.from(main.querySelectorAll('table tr')).find(row => row.querySelector('td')?.textContent?.trim() === 'Categories');
  const category = categories ? Array.from(categories.querySelectorAll('td:nth-child(2) a')).map(a => a.textContent?.trim()).filter(Boolean).join(' > ') : '';
  return { version: 1, url: `${url.origin}${url.pathname}`, title, image: image?.getAttribute('src') || '', price: price ? Number(price[1].replace(/,/g, '')) : null, category, outOfStock: /supplier out of stock|out of stock/i.test(main.textContent || '') };
}

export function makeTakealotBookmarklet(destination: string) {
  const script = `(()=>{try{const capture=${captureTakealotPage.toString()};const data=capture(document,location.href);window.open(${JSON.stringify(destination)}+'#takealot-import='+encodeURIComponent(JSON.stringify(data)),'_blank','noopener');}catch(e){alert(e.message||'读取失败，请确认商品已显示。');}})()`;
  return 'javascript:' + encodeURIComponent(script);
}

export function parseTakealotPageImport(raw: string): ScrapeResult {
  if (raw.length > 20000) throw new Error('导入内容过长');
  const p = JSON.parse(raw);
  const url = new URL(p.url);
  if (p.version !== 1 || url.protocol !== 'https:' || !['takealot.com', 'www.takealot.com'].includes(url.hostname) || url.username || url.password || !/PLID\d+/i.test(url.pathname)) throw new Error('导入链接不是有效的 Takealot 商品页');
  if (typeof p.title !== 'string' || !p.title.trim() || p.title.length > 1000) throw new Error('导入内容缺少商品标题');
  let image: string | null = null;
  if (p.image) { const img = new URL(p.image); if (img.protocol !== 'https:' || img.hostname !== 'media.takealot.com' || !img.pathname.startsWith('/covers_images/')) throw new Error('商品图片来源无效'); image = img.href; }
  if (p.price !== null && (typeof p.price !== 'number' || !Number.isFinite(p.price) || p.price <= 0)) throw new Error('商品售价无效');
  const warnings = ['资料来自你打开的官网商品页，请核对后保存。SKU 仍需卖家资料确认。'];
  if (p.outOfStock) warnings.push('官网显示缺货，展示价不代表当前可购买价格。');
  if (!image) warnings.push('未读到商品主图，请补充图片。');
  if (p.price === null) warnings.push('未读到商品售价，请补充售价。');
  return { normalized_url: `${url.origin}${url.pathname}`, tsin: url.pathname.match(/PLID\d+/i)?.[0].toUpperCase() || null, product_name: p.title.trim(), product_image_url: image, actual_sale_price_zar: p.price, in_stock_price: p.outOfStock ? null : p.price, takealot_category_path: typeof p.category === 'string' ? p.category.slice(0, 1000) || null : null, warnings, success: !!(image && p.price !== null), data_source: 'browser_page' };
}
