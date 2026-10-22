/**
 * EAN-13 校验（与后端 services/label_pdf.py 的 is_ean13 保持一致）。
 *
 * 只有 13 位、全数字、且校验位正确的才能画成可扫的条码。
 * 前端先筛一遍是为了按钮上能显示真实数量——否则后端跳过几个，用户以为出错了。
 */
export function isEan13(code: string | null | undefined): boolean {
  if (!code || code.length !== 13 || !/^\d{13}$/.test(code)) return false;
  let total = 0;
  for (let i = 0; i < 12; i += 1) {
    total += Number(code[i]) * (i % 2 ? 3 : 1);
  }
  return (10 - (total % 10)) % 10 === Number(code[12]);
}
