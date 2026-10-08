#!/usr/bin/env python3
"""Offline Shanghai property-assistant tools. Standard library only; no policy defaults.

Inventory accepts the collected Chinese schema (楼盘/详情) or a list of such records.
Only a current, verified, available unit can yield a confirmed recommendation.
Project-level ranges and unit-price estimates never establish a joint unit match.
Annual interest rates are percentages: --annual-rate 3.1 means 3.1% per year.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path

VERSION = "1.0"
UNKNOWN = {"", "--", "—", "待定", "未知", "页面显示--", "价格待定", "待补", "暂无"}
FEATURE_FIELDS = ("人车分流", "连廊", "游泳池", "阳台封闭", "梯户比", "得房率", "建筑层高", "车位比", "绿化率", "容积率", "装修", "独立电梯厅")


def present(value):
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None and str(value).strip() not in UNKNOWN


def numeric(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def number_range(value):
    """Read a number/range without interpreting annotation numbers as endpoints."""
    if not present(value):
        return None
    if isinstance(value, (int, float)):
        v = numeric(value)
        return [v, v] if v is not None else None
    if isinstance(value, list):
        nums = [numeric(v) for v in value]
        nums = [v for v in nums if v is not None]
        return [min(nums), max(nums)] if nums else None
    s = str(value).replace(",", "").strip()
    # A minimum/maximum-only claim must not become an exact point range.
    if any(token in s for token in ("以上", "以下", "起", "以内", ">", "<")):
        return None
    m = re.match(r"^(?:约|参考)?\s*(\d+(?:\.\d+)?)\s*(?:[-~～—至–]\s*(\d+(?:\.\d+)?))?", s)
    if not m:
        return None
    values = [float(m.group(1)), float(m.group(2) or m.group(1))]
    return sorted(values)


def bedrooms(value):
    if not present(value):
        return []
    if isinstance(value, (int, float)):
        return [int(value)] if value > 0 and int(value) == value else []
    if isinstance(value, list):
        return sorted({v for item in value for v in bedrooms(item)})
    return sorted({int(v) for v in re.findall(r"(\d+)\s*[房室]", str(value))})


def first(mapping, keys):
    for key in keys:
        if present(mapping.get(key)):
            return mapping[key]
    return None


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, result):
    content = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


def normalize(record, inventory_meta):
    details = record.get("详情") or {}
    pairs = {
        "unit_price_yuan_sqm": (first(record, ["展示单价_元每平方米"]), first(details, ["参考单价_元每平方米", "参考单价"]), number_range),
        "price_range_wan": (first(record, ["展示总价_万元"]), first(details, ["参考总价_万元", "参考总价"]), number_range),
        "area_range_sqm": (first(record, ["展示面积_平方米"]), first(details, ["参考面积段_平方米", "参考面积段"]), number_range),
        "sale_status": (record.get("状态"), first(details, ["销售状态", "状态"]), lambda x: "在售" if x in ("在售中", "持续销售", "在售") else x),
        "open_date": (record.get("展示开盘日期"), first(details, ["开盘日期", "开盘时间"]), lambda x: x),
    }
    conflicts, field_sources = [], {}
    for field, (list_value, detail_value, parser) in pairs.items():
        field_sources[field] = {"list": list_value, "detail": detail_value}
        a, b = parser(list_value), parser(detail_value)
        if a is not None and b is not None and a != b:
            conflicts.append({"field": field, "list_value": list_value, "detail_value": detail_value,
                              "message": "列表与详情口径不同，均保留，待核实对应批次和房源。"})
    pr = pairs["price_range_wan"]
    ar = pairs["area_range_sqm"]
    up = pairs["unit_price_yuan_sqm"]
    facts = {
        "address": first(details, ["地址", "地址部分原文"]),
        "unit_price_yuan_sqm": first(details, ["参考单价_元每平方米", "参考单价"]) or record.get("展示单价_元每平方米"),
        "price_range_wan": number_range(pr[1]) or number_range(pr[0]),
        "area_range_sqm": number_range(ar[1]) or number_range(ar[0]) or number_range(details.get("户型建面_平方米")),
        "bedrooms": bedrooms(details.get("销售户型")),
        "sale_status": first(details, ["销售状态", "状态"]) or record.get("状态"),
        "developer": details.get("开发商"), "property_manager": details.get("物业公司"),
        "property_fee": details.get("物业费"), "handover_date": details.get("交房日期"),
        "presale_permit": details.get("预售证"),
        "batch": first(details, ["当前销售批次", "所选批次"]),
        "features": {k: details[k] for k in FEATURE_FIELDS if present(details.get(k))},
        "field_sources": field_sources,
    }
    price_unit = number_range(up[1]) or number_range(up[0])
    if price_unit and facts["area_range_sqm"]:
        a = facts["area_range_sqm"]
        facts["rough_total_estimate_wan"] = [round(price_unit[0] * a[0] / 10000, 2), round(price_unit[1] * a[1] / 10000, 2)]
        facts["estimate_note"] = "单价×面积的粗估，不能证明具体房源总价，未用于判定预算满足。"
    # Only explicit unit records participate in unit-level confirmation. Historical
    # floorplan prices and approved batch totals are deliberately not promoted.
    units = record.get("verified_units", [])
    return {"name": record.get("名称", "未命名楼盘"), "district": record.get("区域"),
            "subdistrict": record.get("板块"), "facts": facts, "conflicts": conflicts,
            "source": record.get("来源") or inventory_meta.get("来源"),
            "collected_at": record.get("采集日期") or inventory_meta.get("采集日期"),
            "units": units, "original": record}


def check(status, reason, evidence=None):
    return {"status": status, "reason": reason, "evidence": evidence}


def range_check(ranges, lower, upper, label):
    valid = [r for r in ranges if r is not None]
    if not valid:
        return check("unknown", f"缺少{label}，不能判为满足。")
    overlap = [r[1] >= lower and r[0] <= upper for r in valid]
    if not any(overlap):
        return check("unmet", f"已采集{label}均在硬性范围之外。", valid)
    if not all(overlap):
        return check("unknown", f"不同页面的{label}结论冲突，需核实。", valid)
    return check("partial", f"项目级{label}有交集，尚未核实同一套在售房源。", valid)


def age_days(raw_date, as_of):
    try:
        age = (as_of - date.fromisoformat(str(raw_date)[:10])).days
        return age if age >= 0 else None
    except (ValueError, TypeError):
        return None


def unit_satisfies(unit, hard):
    p, a, b = (numeric(unit.get(k)) for k in ("price_wan", "area_sqm", "bedrooms"))
    if p is None or a is None or b is None or p <= 0 or a <= 0 or b < 1:
        return False
    return (hard.get("budget_min_wan", 0) <= p <= hard["budget_max_wan"]
            and hard.get("area_min_sqm", 0) <= a <= hard.get("area_max_sqm", math.inf)
            and hard.get("bedrooms_min", 0) <= b <= hard.get("bedrooms_max", math.inf))


def validate_customer(customer):
    if not isinstance(customer, dict):
        raise ValueError("客户输入必须是 JSON 对象。")
    hard = customer.get("hard_constraints", {})
    if not isinstance(hard, dict) or not isinstance(customer.get("preferences", {}), dict):
        raise ValueError("hard_constraints 和 preferences 必须是 JSON 对象。")
    for k in ("budget_max_wan", "budget_min_wan", "area_min_sqm", "area_max_sqm", "bedrooms_min", "bedrooms_max"):
        if k in hard and (not isinstance(hard[k], (int, float)) or numeric(hard[k]) is None or numeric(hard[k]) < 0):
            raise ValueError(f"hard_constraints.{k} 必须是非负数。")
    if numeric(hard.get("budget_max_wan")) is None or hard["budget_max_wan"] <= 0:
        raise ValueError("必须提供正数预算上限 hard_constraints.budget_max_wan。")
    for minimum, maximum in (("budget_min_wan", "budget_max_wan"), ("area_min_sqm", "area_max_sqm"), ("bedrooms_min", "bedrooms_max")):
        if hard.get(minimum, 0) > hard.get(maximum, math.inf):
            raise ValueError(f"{minimum} 不能大于 {maximum}。")
    count = customer.get("max_results", 5)
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 5:
        raise ValueError("max_results 必须为 1 到 5 的整数。")
    for obj, k in ((hard, "districts"), (customer.get("preferences", {}), "districts"), (customer.get("preferences", {}), "features")):
        if k in obj and (not isinstance(obj[k], list) or not all(isinstance(x, str) for x in obj[k])):
            raise ValueError(f"{k} 必须为字符串数组。")
    max_age = customer.get("max_data_age_days", 30)
    if not isinstance(max_age, int) or isinstance(max_age, bool) or max_age < 0:
        raise ValueError("max_data_age_days 必须是非负整数。")
    return hard


def match_inventory(inventory, customer):
    hard = validate_customer(customer)
    as_of = date.fromisoformat(customer.get("as_of") or date.today().isoformat())
    max_age = customer.get("max_data_age_days", 30)
    pref = customer.get("preferences", {})
    meta = inventory if isinstance(inventory, dict) else {}
    records = inventory.get("楼盘", []) if isinstance(inventory, dict) else inventory
    if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
        raise ValueError("库存必须是楼盘数组或包含 楼盘 数组的对象。")
    candidates, excluded = [], []
    for record in records:
        r = normalize(record, meta)
        f = r["facts"]
        raw = f["field_sources"]
        budget_ranges = [number_range(raw["price_range_wan"][k]) for k in ("list", "detail")]
        checks = {"budget": range_check(budget_ranges, hard.get("budget_min_wan", 0), hard["budget_max_wan"], "参考总价")}
        checks["area"] = range_check([number_range(raw["area_range_sqm"][k]) for k in ("list", "detail")] + [number_range((record.get("详情") or {}).get("户型建面_平方米"))], hard.get("area_min_sqm", 0), hard.get("area_max_sqm", math.inf), "面积")
        districts = hard.get("districts", [])
        checks["district"] = (check("met", "未设区域硬性限制；偏好区域只参与排序。") if not districts else
                              check("unknown", "区域未知，不能判为满足。") if not r["district"] else
                              check("met" if r["district"] in districts else "unmet", "按区域硬性条件比较。", r["district"]))
        required_bedrooms = "bedrooms_min" in hard or "bedrooms_max" in hard
        checks["bedrooms"] = (check("met", "未设卧室硬性限制。") if not required_bedrooms else
                               check("unknown", "未采集卧室数量，不能判为满足。") if not f["bedrooms"] else
                               check("partial" if any(hard.get("bedrooms_min", 0) <= b <= hard.get("bedrooms_max", math.inf) for b in f["bedrooms"]) else "unmet", "项目户型卧室数；需与同一套房源价格、面积联核。", f["bedrooms"]))
        sale = f["sale_status"] or ""
        checks["availability"] = check("unmet", "页面明确售罄或停售。", sale) if any(s in sale for s in ("售罄", "停售")) else check("partial" if sale in ("在售中", "持续销售", "在售") else "unknown", "项目状态不代表具体房源现时可售。", sale)
        supported_constraints = {"budget_max_wan", "budget_min_wan", "area_min_sqm", "area_max_sqm", "bedrooms_min", "bedrooms_max", "districts"}
        for key in hard.keys() - supported_constraints:
            checks["additional:" + key] = check("unknown", f"额外硬条件 {key} 尚无自动核验数据，需人工核实。", hard[key])
        # Exact available unit evidence can improve a range-only result, but does
        # not silently resolve contradictory project records.
        good_units = [u for u in r["units"] if isinstance(u, dict) and u.get("available") is True and unit_satisfies(u, hard)]
        fresh_units = [u for u in good_units if u.get("verified") is True and present(u.get("unit_id")) and present(u.get("source")) and age_days(u.get("verified_at"), as_of) is not None and age_days(u.get("verified_at"), as_of) <= max_age]
        for unit in fresh_units:
            for field, unit_key, check_key in (("price_range_wan", "price_wan", "budget"), ("area_range_sqm", "area_sqm", "area")):
                stated_ranges = [number_range(raw[field][k]) for k in ("list", "detail")]
                if any(bounds and not bounds[0] <= numeric(unit[unit_key]) <= bounds[1] for bounds in stated_ranges):
                    r["conflicts"].append({"field": field, "list_value": raw[field]["list"], "detail_value": raw[field]["detail"],
                                           "unit_id": unit["unit_id"], "unit_value": unit[unit_key],
                                           "message": "逐套数据与项目区间不同，保留两者，需核实批次与时点。"})
                    checks[check_key] = check("unknown", "逐套数据与项目区间结论不同，不能静默覆盖。")
            if checks["availability"]["status"] == "unmet":
                r["conflicts"].append({"field": "sale_status", "list_value": raw["sale_status"]["list"], "detail_value": raw["sale_status"]["detail"],
                                       "unit_id": unit["unit_id"], "unit_value": "available=true", "message": "项目显示停售/售罄，但逐套记录显示可售，需核实。"})
                checks["availability"] = check("unknown", "项目和逐套可售状态冲突。")
        if fresh_units and not r["conflicts"] and checks["district"]["status"] == "met":
            for key in ("budget", "area", "bedrooms", "availability"):
                checks[key] = check("met", "同一套已核实在售房源满足此条件。", [u.get("unit_id") for u in fresh_units])
        failures = [k for k, v in checks.items() if v["status"] == "unmet"]
        if failures:
            excluded.append({"name": r["name"], "district": r["district"], "reason": "硬性条件不符", "failed_checks": failures, "checks": checks, "conflicts": r["conflicts"]})
            continue
        quality_fields = [r["district"], r["source"], r["collected_at"], f["address"], f["unit_price_yuan_sqm"], f["price_range_wan"], f["area_range_sqm"], f["bedrooms"] or None, f["developer"], f["property_manager"], f["property_fee"], f["handover_date"], f["presale_permit"], f["sale_status"]]
        known = sum(present(v) for v in quality_fields)
        completeness = {"known_fields": known, "total_fields": len(quality_fields), "percent": round(100 * known / len(quality_fields), 1), "note": "字段有值比例，不代表准确率或采集全部完成。"}
        verification = [v["reason"] for v in checks.values() if v["status"] != "met"]
        verification += [c["message"] + " 字段：" + c["field"] for c in r["conflicts"]]
        age = age_days(r["collected_at"], as_of)
        if age is None or age > max_age:
            verification.append("采集日期缺失、异常或超出新鲜度阈值，需重新核实价格和可售状态。")
        confirmed = bool(fresh_units) and all(c["status"] == "met" for c in checks.values()) and not r["conflicts"]
        if not confirmed:
            verification.append("向案场取得同一套房的房号、当前总价、建筑面积、卧室数及实时可售确认。")
        reasons, components = [], {}
        components["evidence"] = sum({"met": 8, "partial": 4, "unknown": 0}[v["status"]] for v in checks.values())
        components["completeness"] = round(known / len(quality_fields) * 20, 2)
        components["preferred_district"] = 20 if r["district"] in pref.get("districts", []) else 0
        if components["preferred_district"]:
            reasons.append("位于客户偏好区域：" + r["district"])
        matched_features = []
        for feature in pref.get("features", []):
            if feature == "人车分流" and f["features"].get("人车分流") in ("是", "有"):
                matched_features.append(feature)
            elif feature == "无连廊" and f["features"].get("连廊") in ("无", "否", "无连廊"):
                matched_features.append(feature)
            elif feature == "游泳池" and f["features"].get("游泳池") not in (None, "无", "否"):
                matched_features.append(feature)
        components["preferred_features"] = min(15, 5 * len(matched_features))
        components["freshness"] = 5 if age is not None and age <= max_age else 0
        if matched_features:
            reasons.append("页面已显示偏好配置：" + "、".join(matched_features))
        reasons.append("已核实具体房源满足所设硬条件。" if confirmed else "可进一步核验的候选；尚未确认满足全部购房条件。")
        candidates.append({"name": r["name"], "district": r["district"], "subdistrict": r["subdistrict"],
                           "status": "confirmed" if confirmed else "conditional", "score": round(sum(components.values()), 2),
                           "score_components": components, "reasons": reasons, "facts": f, "checks": checks,
                           "conflicts": r["conflicts"], "verification_items": list(dict.fromkeys(verification)),
                           "source": r["source"], "collected_at": r["collected_at"], "data_age_days": age,
                           "completeness": completeness, "matching_units": fresh_units if confirmed else []})
    candidates.sort(key=lambda c: (c["status"] == "confirmed", c["score"]), reverse=True)
    selected = candidates[:customer.get("max_results", 5)]
    return {"schema_version": VERSION, "type": "property_match", "as_of": as_of.isoformat(),
            "is_simulation": bool(customer.get("is_simulation")), "customer": customer,
            "inventory_is_synthetic": bool(meta.get("is_synthetic")),
            "summary": {"inventory_count": len(records), "candidate_count": len(candidates), "excluded_count": len(excluded),
                        "recommended_count": len(selected), "confirmed_count": sum(c["status"] == "confirmed" for c in selected),
                        "conditional_count": sum(c["status"] == "conditional" for c in selected),
                        "shortfall": max(0, min(3, customer.get("max_results", 5)) - len(selected))},
            "recommendations": selected, "excluded": excluded,
            "ranking_note": "排序分仅用于候选优先级，非购房适合度百分比；佣金未参与排序。缺失数据不算条件满足。candidate_count 是未发现已知硬冲突的项目数，包含资料不足的待核项目。",
            "scope_note": "确认仅覆盖输入的硬条件；购房资格、通勤、学校、税费和贷款审批需另行核实。项目范围相交不证明同一套房同时满足条件。"}


def decimal_value(value, name):
    try:
        v = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{name} 必须是有限数值。") from exc
    if not v.is_finite():
        raise ValueError(f"{name} 必须是有限数值。")
    return v


def money(value):
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def mortgage(price_wan, down_payment_wan, annual_rate, years, method="equal-payment"):
    price = (decimal_value(price_wan, "总价") * 10000).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    down = (decimal_value(down_payment_wan, "首付") * 10000).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    annual = decimal_value(annual_rate, "年利率")
    term = decimal_value(years, "年限") * 12
    if price <= 0 or down < 0 or down > price or annual < 0 or annual > 100:
        raise ValueError("总价必须大于0，首付须介于0和总价之间，年利率须介于0%和100%。")
    if term <= 0 or term != term.to_integral_value() or term > 1200:
        raise ValueError("贷款期限需为完整月数，且介于1和1200个月。")
    if method not in ("equal-payment", "equal-principal"):
        raise ValueError("还款方式必须为 equal-payment 或 equal-principal。")
    principal = price - down
    months = int(term)
    rate = annual / Decimal(1200)
    schedule = []
    with localcontext() as ctx:
        ctx.prec = 40
        payment = principal / months if rate == 0 else principal * rate / (1 - (1 + rate) ** (-months))
        remaining = principal
        for month in range(1, months + 1):
            interest = (remaining * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            repay = principal / months if method == "equal-principal" else payment - interest
            repay = min(remaining, max(Decimal(0), repay.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)))
            if month == months:
                repay = remaining
            remaining -= repay
            schedule.append({"month": month, "payment_yuan": money(repay + interest), "principal_yuan": money(repay),
                             "interest_yuan": money(interest), "remaining_principal_yuan": money(remaining)})
    total_interest = sum(Decimal(str(row["interest_yuan"])) for row in schedule)
    total_payment = principal + total_interest
    return {"schema_version": VERSION, "type": "mortgage", "currency": "CNY", "amount_unit": "元",
            "inputs": {"price_wan": float(price / 10000), "down_payment_wan": float(down / 10000),
                       "annual_rate_percent": float(annual), "years": float(term / 12), "method": method},
            "loan_principal_yuan": money(principal), "months": months,
            "first_payment_yuan": schedule[0]["payment_yuan"], "last_payment_yuan": schedule[-1]["payment_yuan"],
            "total_interest_yuan": money(total_interest), "total_payment_yuan": money(total_payment),
            "final_balance_yuan": schedule[-1]["remaining_principal_yuan"], "schedule": schedule,
            "notes": ["按输入固定年利率与按月还款测算；每月金额保留到分，最后一期调平本金。",
                      "未判断首付比例政策、购房资格或银行审批；不包含税费、保险、物业费及提前还款。",
                      "如为组合贷款，分别计算各部分并按月份合计；利率调整后应重新测算。"]}


def text_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [v for v in value if v.strip()]
    raise ValueError("带看反馈中的条目须为文字或文字数组。")


def viewing_report(data):
    visits = data.get("visits", [])
    if not isinstance(visits, list) or not visits:
        raise ValueError("visits 必须包含至少一条带看记录。")
    reports = []
    for visit in visits:
        if not visit.get("project"):
            raise ValueError("每条带看记录需要 project 楼盘名称。")
        likes, dislikes = text_list(visit.get("likes")), text_list(visit.get("dislikes"))
        questions, unresolved = text_list(visit.get("questions")), text_list(visit.get("unverified"))
        # Unanswered questions remain unverified. No automatic factual answer is invented.
        to_verify = list(dict.fromkeys(unresolved + questions))
        actions = text_list(visit.get("next_steps"))
        actions += ["核实并回复：" + item for item in to_verify]
        if not actions:
            actions = ["与客户确认是否进入复看，以及最需要进一步核实的项目。"]
        reports.append({"project": visit["project"], "unit": visit.get("unit"), "likes": likes, "dislikes": dislikes,
                        "questions": questions, "to_verify": to_verify, "next_steps": list(dict.fromkeys(actions)),
                        "customer_quote": visit.get("customer_quote"), "intention": visit.get("intention", "未确认"),
                        "note": "客户现场感受与反馈记录，不能当作项目事实或交易承诺。"})
    return {"schema_version": VERSION, "type": "viewing_report", "is_simulation": bool(data.get("is_simulation")),
            "customer_label": data.get("customer_label"), "viewing_date": data.get("viewing_date"),
            "projects": reports, "preference_updates": data.get("preference_updates", {}),
            "summary": {"visited_projects": len(reports), "unverified_items": sum(len(p["to_verify"]) for p in reports)},
            "next_steps": list(dict.fromkeys(a for p in reports for a in p["next_steps"])),
            "scope_note": "按录入的现场反馈整理；未录入的感受、承诺或问题不推断。"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("match", help="按明确硬条件筛选，区分已确认与待核候选")
    p.add_argument("--inventory", required=True)
    p.add_argument("--customer", required=True)
    p.add_argument("--output")
    p = commands.add_parser("mortgage", help="固定利率按月还款测算；annual-rate 单位为百分比")
    p.add_argument("--price-wan", required=True)
    p.add_argument("--down-payment-wan", required=True)
    p.add_argument("--annual-rate", required=True)
    p.add_argument("--years", required=True)
    p.add_argument("--method", choices=["equal-payment", "equal-principal"], default="equal-payment")
    p.add_argument("--output")
    p = commands.add_parser("viewing", help="从明确填写的反馈生成带看复盘结构")
    p.add_argument("--input", required=True)
    p.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        input_paths = ([args.inventory, args.customer] if args.command == "match" else [args.input] if args.command == "viewing" else [])
        if args.output and any(Path(args.output).resolve() == Path(p).resolve() for p in input_paths):
            raise ValueError("输出路径不得覆盖输入文件，请选择新文件。")
        if args.command == "match":
            result = match_inventory(read_json(args.inventory), read_json(args.customer))
        elif args.command == "mortgage":
            result = mortgage(args.price_wan, args.down_payment_wan, args.annual_rate, args.years, args.method)
        else:
            result = viewing_report(read_json(args.input))
        write_json(args.output, result)
    except (ValueError, OSError, TypeError) as exc:
        parser.exit(2, f"错误：{exc}\n")


if __name__ == "__main__":
    main()
