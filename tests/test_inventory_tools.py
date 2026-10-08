"""Acceptance tests for observation provenance and cross-batch isolation.

Fixtures are synthetic. Set PROPERTY_QA_DIR to keep CLI scratch files in work/qa.
These assertions describe required behavior; failures identify unresolved defects.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tools = load_module("inventory_tools")
assistant = load_module("assistant")


def inventory():
    return {
        "is_synthetic": True,
        "来源": "synthetic-old-source",
        "采集日期": "2026-10-01",
        "楼盘": [{"名称": "虚构批次测试苑", "区域": "浦东新区", "来源": "synthetic-old-source",
                  "采集日期": "2026-10-01", "状态": "在售中",
                  "详情": {"当前销售批次": "一期", "参考总价_万元": "700-900", "参考面积段_平方米": "100-130",
                           "销售户型": "3房2厅", "预售证": "FAKE-PERMIT-PHASE-1", "物业费": "5元/㎡/月"}}]
    }


def observation(batch="一期"):
    return {"source": "synthetic-new-source", "observed_at": "2026-10-08T12:00:00+08:00",
            "updates": [{"name": "虚构批次测试苑", "batch": batch,
                         "fields": {"参考总价_万元": "760-950"}, "status": "虚构已核对"}]}


class AuditTests(unittest.TestCase):
    def test_audit_preserves_list_detail_discrepancy(self):
        data = inventory()
        data["楼盘"][0]["展示面积_平方米"] = "90-120"
        report = tools.audit(data, "2026-10-08")
        conflicts = report["补采队列"][0]["口径差异"]
        self.assertEqual(conflicts[0]["列表口径"], "90-120")
        self.assertEqual(conflicts[0]["详情口径"], "100-130")

    def test_blank_markers_are_missing_core_fields(self):
        data = inventory()
        placeholders = ["   ", "--", "页面显示--", "未知", None, "待定", "暂无", []]
        keys = [aliases[0] for aliases in tools.CORE.values()]
        data["楼盘"][0]["详情"] = dict(zip(keys, placeholders))
        result = tools.audit(data, "2026-10-08")
        self.assertEqual(result["八项核心详情均有值"], 0)
        self.assertEqual(len(result["补采队列"][0]["核心详情缺失"]), 8)
        for value in placeholders:
            self.assertFalse(tools.known(value), repr(value))

    def test_audit_uses_detail_provenance_after_merge(self):
        data, _ = tools.merge(inventory(), observation())
        row = tools.audit(data, "2026-10-08")["补采队列"][0]
        self.assertEqual(row["详情采集日期"], "2026-10-08")
        self.assertEqual(row["来源"], "synthetic-new-source",
                         "新详情采集日期不能配旧列表来源，报告会失去证据对应关系。")


class MergeTests(unittest.TestCase):
    def test_same_observation_is_idempotent(self):
        first, summary = tools.merge(inventory(), observation())
        before = copy.deepcopy(first)
        second, again = tools.merge(first, observation())
        self.assertEqual(summary["status"], "applied")
        self.assertEqual(again["status"], "already_applied")
        self.assertEqual(second, before)
        self.assertEqual(len(second["楼盘"][0]["详情观察历史"]), 1)

    def test_missing_and_empty_fields_are_rejected(self):
        for supplied in (None, {}, []):
            obs = observation()
            if supplied is None:
                obs["updates"][0].pop("fields")
            else:
                obs["updates"][0]["fields"] = supplied
            with self.assertRaises(ValueError):
                tools.merge(inventory(), obs)

    def test_unknown_fields_do_not_erase_known_value(self):
        obs = observation()
        obs["updates"][0]["fields"] = {"物业费": "--", "参考总价_万元": "待定"}
        result, _ = tools.merge(inventory(), obs)
        self.assertEqual(result["楼盘"][0]["详情"]["物业费"], "5元/㎡/月")
        self.assertEqual(result["楼盘"][0]["详情"]["参考总价_万元"], "700-900")

    def test_new_sales_batch_must_not_mix_with_current_detail(self):
        data = inventory()
        original = copy.deepcopy(data)
        with self.assertRaises(ValueError):
            tools.merge(data, observation("二期"))
        self.assertEqual(data, original, "二期观察仅含新价格；拒绝且保留完整原快照。")

    def test_unknown_old_batch_rejects_new_batch_overlay(self):
        data = inventory()
        data["楼盘"][0]["详情"].pop("当前销售批次")
        original = copy.deepcopy(data)
        with self.assertRaises(ValueError):
            tools.merge(data, observation("二期"))
        self.assertEqual(data, original)

    def test_batch_alias_cannot_override_declared_matching_batch(self):
        for alias in ("当前销售批次", "所选批次"):
            with self.subTest(alias=alias):
                obs = observation("一期")
                obs["updates"][0]["fields"][alias] = "二期"
                with self.assertRaises(ValueError, msg="batch与fields里的批次别名冲突必须拒绝。"):
                    tools.merge(inventory(), obs)

    def test_two_field_aliases_must_not_disagree(self):
        obs = observation("一期")
        obs["updates"][0].pop("batch")
        obs["updates"][0]["fields"].update({"当前销售批次": "一期", "所选批次": "二期"})
        with self.assertRaises(ValueError):
            tools.merge(inventory(), obs)

    def test_invalid_later_update_does_not_partially_mutate_inventory(self):
        data = inventory()
        original = copy.deepcopy(data)
        obs = observation()
        obs["updates"].append({"name": "虚构批次测试苑", "batch": "一期"})
        with self.assertRaises(ValueError):
            tools.merge(data, obs)
        self.assertEqual(data, original, "批次整体失败必须保持原数据，不得留下部分已应用更新。")


class SkillScenarioAndCliTests(unittest.TestCase):
    def test_800_wan_three_bedrooms_commute_unknown_remains_conditional(self):
        synthetic = json.loads((ROOT / "examples/synthetic_inventory.json").read_text())
        customer = {"is_simulation": True, "label": "QA模拟客户", "as_of": "2026-10-08",
                    "hard_constraints": {"budget_max_wan": 800, "bedrooms_min": 3, "commute_max_minutes": 45}}
        with tempfile.TemporaryDirectory(prefix="property-qa-", dir=os.environ.get("PROPERTY_QA_DIR")) as tmp:
            tmp = Path(tmp)
            customer_path = tmp / "customer.json"
            inventory_path = tmp / "inventory.json"
            customer_path.write_text(json.dumps(customer, ensure_ascii=False), encoding="utf-8")
            inventory_path.write_text(json.dumps(synthetic, ensure_ascii=False), encoding="utf-8")
            output = tmp / "match.json"
            proc = subprocess.run([sys.executable, str(ROOT / "scripts/assistant.py"), "match", "--inventory", str(inventory_path),
                                   "--customer", str(customer_path), "--output", str(output)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(output.read_text())
        self.assertEqual(report["summary"]["confirmed_count"], 0)
        self.assertEqual(report["summary"]["recommended_count"], 1)
        self.assertEqual(report["summary"]["shortfall"], 2)
        candidate = report["recommendations"][0]
        self.assertEqual(candidate["name"], "模拟桂雨庭（虚构项目）")
        self.assertEqual(candidate["checks"]["budget"]["status"], "unknown")
        self.assertEqual(candidate["checks"]["additional:commute_max_minutes"]["status"], "unknown")
        self.assertEqual(candidate["status"], "conditional")

    def test_audit_queue_csv_cannot_overwrite_source_inventory(self):
        with tempfile.TemporaryDirectory(prefix="property-qa-", dir=os.environ.get("PROPERTY_QA_DIR")) as tmp:
            tmp = Path(tmp)
            source = tmp / "synthetic.json"
            source.write_text(json.dumps(inventory(), ensure_ascii=False), encoding="utf-8")
            original = source.read_bytes()
            proc = subprocess.run([sys.executable, str(ROOT / "scripts/inventory_tools.py"), "audit", "--inventory", str(source),
                                   "--as-of", "2026-10-08", "--output", str(tmp / "audit.json"), "--queue-csv", str(source)], capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0, "queue-csv 是写入出口，也需要检查路径碰撞。")
            self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
