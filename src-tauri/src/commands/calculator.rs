use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CalcInput {
    pub length_mm: Option<f64>,
    pub width_mm: Option<f64>,
    pub height_mm: Option<f64>,
    pub actual_weight_kg: Option<f64>,
    pub actual_sale_price_zar: Option<f64>,
    pub purchase_cost_cny: Option<f64>,
    pub purchase_shipping_cny: Option<f64>,
    pub purchase_quantity: Option<i32>,
    pub packaging_cost_per_unit_cny: Option<f64>,
    pub shipping_method: Option<String>,
    pub success_fee_rate: Option<f64>,
    pub inbound_listing_fee_cny: Option<f64>,
    pub outbound_operation_fee_cny: Option<f64>,
    pub last_mile_delivery_fee_cny: Option<f64>,
    pub other_fee_cny: Option<f64>,
    pub fulfillment_fee_zar: Option<f64>,
    pub fee_category: Option<String>,
    pub fee_category_confirmed: Option<bool>,
    pub manual_domestic_forwarding_cny: Option<f64>,
    pub manual_international_shipping_cny: Option<f64>,
    pub manual_overseas_op_cost_cny: Option<f64>,
    pub manual_success_fee_zar: Option<f64>,
    pub manual_fulfillment_fee_zar: Option<f64>,
    pub manual_total_cost_zar: Option<f64>,
}

pub fn calculate_all(input: &CalcInput, exchange_rate: f64) -> HashMap<String, serde_json::Value> {
    let mut r: HashMap<String, serde_json::Value> = HashMap::new();
    let n = serde_json::Value::Null;

    let qty = input.purchase_quantity.unwrap_or(4) as f64;
    let packaging = input.packaging_cost_per_unit_cny.unwrap_or(1.0);
    let inbound = input.inbound_listing_fee_cny.unwrap_or(0.75);
    let outbound = input.outbound_operation_fee_cny.unwrap_or(0.70);
    let last_mile = input.last_mile_delivery_fee_cny.unwrap_or(2.0);
    let other = input.other_fee_cny.unwrap_or(2.0);
    let mut fulfillment = input.fulfillment_fee_zar.unwrap_or(42.0);

    if let Some(mf) = input.manual_fulfillment_fee_zar {
        fulfillment = mf;
    }

    // Volume CBM
    if let (Some(l), Some(w), Some(h)) = (input.length_mm, input.width_mm, input.height_mm) {
        let v = (l * w * h) / 1_000_000_000.0;
        r.insert("volume_cbm".into(), serde_json::json!(round6(v)));
    } else {
        r.insert("volume_cbm".into(), n.clone());
    }

    // Volumetric weight: volume_cbm * 1000 / 6
    let vol_wt = r.get("volume_cbm").and_then(|v| v.as_f64()).map(|vc| round3(vc * 1000.0 / 6.0));
    r.insert("volumetric_weight_kg".into(), vol_wt.map_or(n.clone(), |x| serde_json::json!(x)));

    // Chargeable weight
    let chargeable = match (vol_wt, input.actual_weight_kg) {
        (Some(vw), Some(aw)) => Some(round3(vw.max(aw))),
        (Some(vw), None) => Some(round3(vw)),
        (None, Some(aw)) => Some(aw),
        (None, None) => None,
    };
    r.insert("chargeable_weight_kg".into(), chargeable.map_or(n.clone(), |x| serde_json::json!(x)));

    // Domestic forwarding cost
    let dom_fwd = chargeable.map(|cw| {
        let raw = 5.0 * cw;
        if raw > 1.0 { round2(raw) } else { 1.0 }
    });
    let dom_fwd = if let Some(md) = input.manual_domestic_forwarding_cny { Some(md) } else { dom_fwd };
    r.insert("domestic_forwarding_cost_per_unit_cny".into(), dom_fwd.map_or(n.clone(), |x| serde_json::json!(x)));

    // Unit product cost
    let unit_cost = if let (Some(pc), Some(ps), Some(df)) = (input.purchase_cost_cny, input.purchase_shipping_cny, dom_fwd) {
        if qty > 0.0 {
            Some(round2((pc + ps) / qty + packaging + df))
        } else { None }
    } else { None };
    r.insert("unit_product_cost_cny".into(), unit_cost.map_or(n.clone(), |x| serde_json::json!(x)));

    // International shipping
    let intl = if let (Some(sm), Some(cw)) = (input.shipping_method.as_deref(), chargeable) {
        match sm {
            "空运普货" => Some(round2(79.0 * cw)),
            "空运带电" => Some(round2(89.0 * cw)),
            "海运普货" => {
                r.get("volume_cbm").and_then(|v| v.as_f64()).map(|vc| round2(vc * 1500.0))
            }
            "海运带电" => {
                r.get("volume_cbm").and_then(|v| v.as_f64()).map(|vc| round2(vc * 2100.0))
            }
            _ => None,
        }
    } else { None };
    let intl = if let Some(mi) = input.manual_international_shipping_cny { Some(mi) } else { intl };
    r.insert("international_shipping_per_unit_cny".into(), intl.map_or(n.clone(), |x| serde_json::json!(x)));

    // Cost to SA warehouse
    let cny_sa = if let (Some(uc), Some(is)) = (unit_cost, intl) {
        Some(round2(uc + is))
    } else { None };
    r.insert("cost_to_sa_warehouse_cny".into(), cny_sa.map_or(n.clone(), |x| serde_json::json!(x)));
    let zar_sa = cny_sa.map(|c| round2(c / exchange_rate));
    r.insert("cost_to_sa_warehouse_zar".into(), zar_sa.map_or(n.clone(), |x| serde_json::json!(x)));

    // Overseas warehouse ops
    let overseas_cny = round2(inbound + outbound + last_mile + other);
    let overseas_cny = if let Some(mo) = input.manual_overseas_op_cost_cny { mo } else { overseas_cny };
    r.insert("overseas_warehouse_operation_cost_cny".into(), serde_json::json!(overseas_cny));
    r.insert("overseas_warehouse_operation_cost_zar".into(), serde_json::json!(round2(overseas_cny / exchange_rate)));

    // Success fee & official total
    let (sf, _ot_raw) = if let (Some(fr), Some(sp)) = (input.success_fee_rate, input.actual_sale_price_zar) {
        let sf_val = round2(fr * sp);
        (Some(sf_val), Some(round2(sf_val + fulfillment)))
    } else { (None, None) };
    let sf = if let Some(ms) = input.manual_success_fee_zar { Some(ms) } else { sf };
    let ot = sf.map(|s| round2(s + fulfillment));
    r.insert("success_fee_zar".into(), sf.map_or(n.clone(), |x| serde_json::json!(x)));
    r.insert("official_total_cost_zar".into(), ot.map_or(n.clone(), |x| serde_json::json!(x)));

    // Total cost, profit, margin
    if let (Some(zar_sa), Some(o_t)) = (zar_sa, ot) {
        let ov_zar = r.get("overseas_warehouse_operation_cost_zar").and_then(|x| x.as_f64()).unwrap_or(0.0);
        let tc = round2(zar_sa + ov_zar + o_t);
        r.insert("total_cost_zar".into(), serde_json::json!(tc));

        if let Some(sp) = input.actual_sale_price_zar {
            let profit = round2(sp - tc);
            r.insert("profit_zar".into(), serde_json::json!(profit));
            if sp > 0.0 {
                r.insert("profit_margin".into(), serde_json::json!(round4(1.0 - tc / sp)));
            } else {
                r.insert("profit_margin".into(), n.clone());
            }
        }
    }
    // Manual total cost override
    if let Some(mt) = input.manual_total_cost_zar {
        r.insert("total_cost_zar".into(), serde_json::json!(mt));
        if let Some(sp) = input.actual_sale_price_zar {
            r.insert("profit_zar".into(), serde_json::json!(round2(sp - mt)));
            if sp > 0.0 {
                r.insert("profit_margin".into(), serde_json::json!(round4(1.0 - mt / sp)));
            }
        }
    }

    // Minimum price at 20% / 15% margin
    let min20 = compute_min_price(&r, input, 0.20);
    let min15 = compute_min_price(&r, input, 0.15);
    r.insert("minimum_price_at_20_margin".into(), min20.map_or(n.clone(), |x| serde_json::json!(x)));
    r.insert("minimum_price_at_15_margin".into(), min15.map_or(n.clone(), |x| serde_json::json!(x)));

    r
}

fn compute_min_price(r: &HashMap<String, serde_json::Value>, input: &CalcInput, target: f64) -> Option<f64> {
    let margin = r.get("profit_margin")?.as_f64()?;
    let sale_price = input.actual_sale_price_zar?;
    let fee_rate = input.success_fee_rate?;
    let profit = r.get("profit_zar")?.as_f64()?;
    if margin > target && (1.0 - fee_rate - target).abs() > 1e-10 {
        let numerator = (1.0 - fee_rate) * sale_price - profit;
        let denominator = (1.0 - fee_rate) - target;
        if denominator != 0.0 {
            Some(numerator / denominator)
        } else { None }
    } else { None }
}

pub fn determine_selection_status(input: &CalcInput, margin: Option<f64>) -> String {
    let required = [
        input.actual_sale_price_zar,
        input.purchase_cost_cny,
        input.purchase_shipping_cny,
        input.length_mm,
        input.width_mm,
        input.height_mm,
        input.actual_weight_kg,
    ];
    if required.iter().any(|x| x.is_none()) {
        return "数据待补充".to_string();
    }
    if input.purchase_quantity.unwrap_or(0) == 0 {
        return "数据待补充".to_string();
    }
    if !input.fee_category_confirmed.unwrap_or(false) || input.fee_category.as_deref().unwrap_or("").is_empty() {
        return "待确认品类".to_string();
    }
    match margin {
        Some(m) if m >= 0.25 => "合格选品".to_string(),
        Some(_) => "不建议选品".to_string(),
        None => "数据待补充".to_string(),
    }
}

fn round2(v: f64) -> f64 { (v * 100.0).round() / 100.0 }
fn round3(v: f64) -> f64 { (v * 1000.0).round() / 1000.0 }
fn round4(v: f64) -> f64 { (v * 10000.0).round() / 10000.0 }
fn round6(v: f64) -> f64 { (v * 1000000.0).round() / 1000000.0 }
