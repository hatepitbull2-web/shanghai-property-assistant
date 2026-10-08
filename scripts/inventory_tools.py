#!/usr/bin/env python3
"""Audit inventory coverage and merge dated, reviewed observations without erasing history."""
import argparse
import copy
import csv
import hashlib
import json
from datetime import date
from pathlib import Path

EMPTY = {"", "--", "暂无", "待定", "待采集", "待核实", "页面显示--", "未知", "none", "null"}
CORE = {
    "地址": ("地址",), "总价": ("参考总价_万元", "参考总价"),
    "装修": ("装修", "装修情况"), "开发商": ("开发商",),
    "物业公司": ("物业公司",), "物业费": ("物业费",),
    "交付日期": ("交房日期", "交房时间"), "预售证": ("预售证",),
}


def known(value):
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in EMPTY
    return bool(value) if isinstance(value, (list, dict)) else True


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, obj):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)


def audit(data, as_of):
    rows = data["楼盘"]
    result = []
    for row in rows:
        detail = row.get("详情", {})
        missing = [label for label, keys in CORE.items() if not any(known(detail.get(k)) for k in keys)]
        collected = row.get("详情采集日期", row.get("采集日期", data.get("采集日期")))
        try:
            age = (date.fromisoformat(as_of) - date.fromisoformat(collected[:10])).days
        except (ValueError, TypeError):
            age = None
        conflicts = []
        for top, detail_keys in [("展示单价_元每平方米", ("参考单价_元每平方米", "参考单价")),
                                  ("展示面积_平方米", ("参考面积段_平方米", "参考面积段")),
                                  ("展示开盘日期", ("开盘日期", "开盘时间")),
                                  ("展示房源套数", ("本期房源套数", "本期房源数"))]:
            value = next((detail[k] for k in detail_keys if known(detail.get(k))), None)
            if known(row.get(top)) and known(value) and str(row[top]) != str(value):
                conflicts.append({"字段": top, "列表口径": row[top], "详情口径": value,
                                  "处理": "保留两种口径，可能属于不同批次，待核实"})
        result.append({"名称": row["名称"], "状态": row.get("状态"),
                       "核心详情缺失": missing, "详情字段数": len(detail),
                       "详情采集日期": collected, "距采集天数": age,
                       "来源": row.get("详情来源", row.get("来源", data.get("来源"))),
                       "口径差异": conflicts,
                       "优先级": "P0" if len(missing) > 4 else "P1" if missing else "P2",
                       "说明": "有值率不等于来源已核验；完整详情及附件仍需逐项检查"})
    result.sort(key=lambda x: (x["优先级"], -len(x["核心详情缺失"]), x["名称"]))
    names = [r["名称"] for r in rows]
    return {"审计日期": as_of, "目录数": len(rows),
            "已含详情": sum(bool(x.get("详情")) for x in rows),
            "八项核心详情均有值": sum(not x["核心详情缺失"] for x in result),
            "同名重复": sorted({n for n in names if names.count(n) > 1}),
            "覆盖边界": "此统计只覆盖导入文件，不能据此认定上海全量或当前在售全量。",
            "补采队列": result}


def merge(data, observation):
    source = observation.get("source")
    observed_at = observation.get("observed_at")
    if not source or not observed_at:
        raise ValueError("补采批次必须有source和observed_at")
    date.fromisoformat(observed_at[:10])
    updates = observation.get("updates")
    if not isinstance(updates, list) or not updates:
        raise ValueError("updates必须为非空数组")
    # Failure must not mutate the caller's object or a prior successful snapshot.
    data = copy.deepcopy(data)
    lookup = {}
    for row in data["楼盘"]:
        if row["名称"] in lookup:
            raise ValueError("同名项目必须先消歧后再合并")
        lookup[row["名称"]] = row
    digest = hashlib.sha256(json.dumps(observation, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    log = data.setdefault("补采批次日志", [])
    if any(x["sha256"] == digest for x in log):
        return data, {"status": "already_applied", "sha256": digest}
    for update in updates:
        name = update.get("name")
        if name not in lookup:
            raise ValueError(f"未在目录找到项目：{name}；新增项目请先建立目录记录")
        fields = update.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise ValueError(f"{name}的fields必须为非空对象")
        project = lookup[name]
        current_detail = project.get("详情", {})
        current_batch = current_detail.get("当前销售批次", current_detail.get("所选批次"))
        batch_labels = [x for x in (update.get("batch"), fields.get("当前销售批次"), fields.get("所选批次")) if known(x)]
        if len(set(batch_labels)) > 1:
            raise ValueError(f"{name}补采批次标识互相矛盾，不能合并")
        new_batch = batch_labels[0] if batch_labels else None
        if current_batch and new_batch and current_batch != new_batch:
            raise ValueError(f"{name}批次不同，必须单独建立批次快照，不能覆盖当前详情")
        if new_batch and not current_batch and current_detail:
            raise ValueError(f"{name}旧详情批次未知，不能把新批次与旧详情合并")
        # Preserve raw observations; unknowns do not erase known historical values.
        project.setdefault("详情观察历史", []).append({"source": source, "observed_at": observed_at,
            "batch": update.get("batch"), "fields": fields, "sha256": digest})
        detail = project.setdefault("详情", {})
        for key, value in fields.items():
            if known(value):
                detail[key] = value
        project["详情采集日期"] = observed_at[:10]
        project["详情来源"] = source
        project["详情采集状态"] = update.get("status", "本次可见字段已补采，其余仍待核")
    log.append({"source": source, "observed_at": observed_at, "sha256": digest, "projects": len(updates)})
    return data, {"status": "applied", "projects": len(updates), "sha256": digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("audit")
    a.add_argument("--inventory", required=True)
    a.add_argument("--as-of", required=True, help="YYYY-MM-DD，使用业务日期")
    a.add_argument("--output", required=True)
    a.add_argument("--queue-csv")
    m = sub.add_parser("merge")
    m.add_argument("--inventory", required=True)
    m.add_argument("--observations", required=True)
    m.add_argument("--output", required=True, help="必须不同于原始文件；核对后再使用新版本")
    args = parser.parse_args()
    if Path(args.output).resolve() == Path(args.inventory).resolve():
        parser.error("输出必须不同于原始库存，保留来源快照")
    if args.command == "audit" and args.queue_csv:
        if Path(args.queue_csv).resolve() in {Path(args.inventory).resolve(), Path(args.output).resolve()}:
            parser.error("CSV输出不能覆盖输入库存或JSON审计结果")
    if args.command == "merge" and Path(args.output).resolve() == Path(args.observations).resolve():
        parser.error("输出不能覆盖原始补采记录")
    try:
        data = read(args.inventory)
        if args.command == "audit":
            result = audit(data, args.as_of)
            save(args.output, result)
            if args.queue_csv:
                with Path(args.queue_csv).open("w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["优先级", "楼盘", "缺失核心字段", "采集日期", "距采集天数"])
                    for row in result["补采队列"]:
                        writer.writerow([row["优先级"], row["名称"], "、".join(row["核心详情缺失"]), row["详情采集日期"], row["距采集天数"]])
            print(json.dumps({k:v for k,v in result.items() if k != "补采队列"}, ensure_ascii=False))
        else:
            merged, summary = merge(data, read(args.observations))
            save(args.output, merged)
            print(json.dumps(summary, ensure_ascii=False))
    except (KeyError, ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
