"""Behavior tests for unknowns, contradictory evidence and amortization."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("assistant", ROOT / "scripts" / "assistant.py")
assistant = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assistant)


def customer():
    return {"as_of": "2026-10-08", "hard_constraints": {"budget_max_wan": 1000, "area_min_sqm": 100, "area_max_sqm": 150, "bedrooms_min": 3}, "max_results": 5}


def project():
    return {"名称": "完全虚构测试项目", "区域": "浦东新区", "状态": "在售中", "展示单价_元每平方米": 70000,
            "展示面积_平方米": "100-140", "采集日期": "2026-10-08", "来源": "synthetic-test",
            "详情": {"参考总价_万元": "700-980", "参考面积段_平方米": "100-140", "销售户型": "3房2厅"}}


class MatchTests(unittest.TestCase):
    def run_match(self, records, request=None):
        return assistant.match_inventory({"is_synthetic": True, "楼盘": records}, request or customer())

    def test_range_intersection_is_not_confirmed_unit(self):
        out = self.run_match([project()])
        self.assertEqual(out["recommendations"][0]["status"], "conditional")
        self.assertEqual(out["summary"]["shortfall"], 2)

    def test_unknown_price_not_satisfied_by_estimate(self):
        p = project()
        p["详情"]["参考总价_万元"] = "待定"
        c = self.run_match([p])["recommendations"][0]
        self.assertEqual(c["checks"]["budget"]["status"], "unknown")
        self.assertIsNone(c["facts"]["price_range_wan"])
        self.assertEqual(c["facts"]["rough_total_estimate_wan"], [700, 980])
        self.assertEqual(c["status"], "conditional")

    def test_unknown_bedroom_does_not_pass(self):
        p = project()
        del p["详情"]["销售户型"]
        self.assertEqual(self.run_match([p])["recommendations"][0]["checks"]["bedrooms"]["status"], "unknown")

    def test_budget_conflict_is_retained_not_resolved_silently(self):
        p = project()
        p["展示总价_万元"] = "700-900"
        p["详情"]["参考总价_万元"] = "1100-1400"
        c = self.run_match([p])["recommendations"][0]
        self.assertEqual(c["checks"]["budget"]["status"], "unknown")
        self.assertEqual(c["status"], "conditional")
        self.assertEqual(c["conflicts"][0]["list_value"], "700-900")
        self.assertEqual(c["conflicts"][0]["detail_value"], "1100-1400")

    def test_hard_mismatches_excluded_and_not_padded(self):
        variants = []
        for changes in ({"参考总价_万元": "1100-1200"}, {"参考面积段_平方米": "40-80"}, {"销售户型": "2房2厅"}):
            p = project()
            p["详情"].update(changes)
            if "参考面积段_平方米" in changes:
                p["展示面积_平方米"] = "40-80"
            variants.append(p)
        out = self.run_match(variants)
        self.assertEqual(out["recommendations"], [])
        self.assertEqual(out["summary"]["excluded_count"], 3)

    def test_preferred_district_is_not_hard_exclusion(self):
        req = customer()
        req["preferences"] = {"districts": ["普陀区"]}
        self.assertEqual(len(self.run_match([project()], req)["recommendations"]), 1)
        req["hard_constraints"]["districts"] = ["普陀区"]
        self.assertEqual(len(self.run_match([project()], req)["recommendations"]), 0)

    def test_additional_hard_constraint_not_silently_ignored(self):
        req = customer()
        req["hard_constraints"]["commute_max_minutes"] = 45
        out = self.run_match([project()], req)["recommendations"][0]
        self.assertEqual(out["checks"]["additional:commute_max_minutes"]["status"], "unknown")
        self.assertTrue(any("commute_max_minutes" in s for s in out["verification_items"]))

    def test_fresh_verified_same_unit_can_confirm(self):
        p = project()
        p["verified_units"] = [{"unit_id": "synthetic-101", "price_wan": 850, "area_sqm": 120, "bedrooms": 3,
                                "available": True, "verified": True, "source": "synthetic-test", "verified_at": "2026-10-08"}]
        self.assertEqual(self.run_match([p])["recommendations"][0]["status"], "confirmed")
        p["verified_units"][0]["available"] = False
        self.assertEqual(self.run_match([p])["recommendations"][0]["status"], "conditional")

    def test_no_cross_unit_matching(self):
        p = project()
        p["verified_units"] = [
            {"unit_id": "cheap-small", "price_wan": 700, "area_sqm": 80, "bedrooms": 3, "available": True, "verified": True, "source": "test", "verified_at": "2026-10-08"},
            {"unit_id": "large-expensive", "price_wan": 1200, "area_sqm": 120, "bedrooms": 3, "available": True, "verified": True, "source": "test", "verified_at": "2026-10-08"}]
        self.assertEqual(self.run_match([p])["recommendations"][0]["status"], "conditional")

    def test_contradictory_unit_price_does_not_overwrite_project(self):
        p = project()
        p["详情"]["参考总价_万元"] = "1100-1400"
        p["verified_units"] = [{"unit_id": "synthetic-101", "price_wan": 850, "area_sqm": 120, "bedrooms": 3,
                                "available": True, "verified": True, "source": "test", "verified_at": "2026-10-08"}]
        out = self.run_match([p])["recommendations"][0]
        self.assertEqual(out["status"], "conditional")
        self.assertEqual(out["checks"]["budget"]["status"], "unknown")
        self.assertEqual(out["conflicts"][0]["unit_value"], 850)

    def test_stale_or_future_unit_never_confirms(self):
        p = project()
        for when in ("2026-01-01", "2027-01-01", None):
            p["verified_units"] = [{"unit_id": "synthetic-101", "price_wan": 850, "area_sqm": 120, "bedrooms": 3, "available": True,
                                    "verified": True, "source": "test", "verified_at": when}]
            self.assertEqual(self.run_match([p])["recommendations"][0]["status"], "conditional")

    def test_commission_does_not_change_score_or_rank(self):
        p = project()
        before = self.run_match([p])["recommendations"][0]["score"]
        p["佣金"] = "100%"
        p["详情"]["佣金"] = 10000000
        self.assertEqual(self.run_match([p])["recommendations"][0]["score"], before)

    def test_sample_has_three_conditional_without_padding(self):
        inventory = json.loads((ROOT / "examples/synthetic_inventory.json").read_text())
        req = json.loads((ROOT / "examples/customer.json").read_text())
        out = assistant.match_inventory(inventory, req)
        self.assertEqual(out["summary"]["conditional_count"], 3)
        self.assertEqual(out["summary"]["confirmed_count"], 0)
        self.assertTrue(out["inventory_is_synthetic"])

    def test_parser_does_not_convert_one_sided_claim_to_exact_price(self):
        for value in ("900万起", "1800万元以下", "--", "待定"):
            self.assertIsNone(assistant.number_range(value))
        self.assertEqual(assistant.number_range("约1000-1200（含2套非标）"), [1000, 1200])


class MortgageTests(unittest.TestCase):
    def test_zero_interest_both_methods(self):
        for method in ("equal-payment", "equal-principal"):
            out = assistant.mortgage(100, 40, 0, 10, method)
            self.assertEqual(out["first_payment_yuan"], 5000)
            self.assertEqual(out["last_payment_yuan"], 5000)
            self.assertEqual(out["total_interest_yuan"], 0)
            self.assertEqual(out["final_balance_yuan"], 0)

    def test_independent_equal_payment_known_result(self):
        out = assistant.mortgage(100, 0, 3.6, 30)
        self.assertAlmostEqual(out["first_payment_yuan"], 4546.45, places=2)
        self.assertEqual(out["final_balance_yuan"], 0)
        self.assertAlmostEqual(sum(x["principal_yuan"] for x in out["schedule"]), 1000000, places=5)
        self.assertAlmostEqual(sum(x["payment_yuan"] for x in out["schedule"]), out["total_payment_yuan"], places=5)
        self.assertTrue(all(x["remaining_principal_yuan"] >= 0 for x in out["schedule"]))

    def test_equal_principal_interest_identity(self):
        out = assistant.mortgage(100, 40, 3.6, 10, "equal-principal")
        self.assertEqual(out["first_payment_yuan"], 6800)
        self.assertEqual(out["last_payment_yuan"], 5015)
        self.assertEqual(out["total_interest_yuan"], 108900)
        self.assertEqual(out["final_balance_yuan"], 0)

    def test_rounding_final_month_clears_principal(self):
        out = assistant.mortgage(101.234567, 20, 3.17, 17)
        self.assertEqual(out["final_balance_yuan"], 0)
        self.assertAlmostEqual(sum(r["principal_yuan"] for r in out["schedule"]), out["loan_principal_yuan"], places=5)

    def test_invalid_rates_and_funding_rejected(self):
        for args in ((100, 110, 3, 30), (100, -1, 3, 30), (100, 30, -1, 30), (100, 30, "NaN", 30), (100, 30, 3, 0), (100, 30, 3, 0.01)):
            with self.assertRaises(ValueError):
                assistant.mortgage(*args)

    def test_all_cash_is_zero_loan(self):
        out = assistant.mortgage(100, 100, 3.6, 30)
        self.assertEqual(out["total_payment_yuan"], 0)


class ViewingAndCliTests(unittest.TestCase):
    def test_unanswered_questions_remain_unverified(self):
        out = assistant.viewing_report({"visits": [{"project": "虚构", "likes": ["喜欢采光"], "questions": ["是否含车位？"]}]})
        self.assertIn("是否含车位？", out["projects"][0]["to_verify"])
        self.assertEqual(out["projects"][0]["dislikes"], [])
        self.assertEqual(out["projects"][0]["intention"], "未确认")

    def test_cli_writes_utf8_json_and_does_not_edit_inventory(self):
        inventory = ROOT / "examples/synthetic_inventory.json"
        before = inventory.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "report.json"
            run = subprocess.run([sys.executable, str(ROOT / "scripts/assistant.py"), "match", "--inventory", str(inventory), "--customer", str(ROOT / "examples/customer.json"), "--output", str(output)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(output.read_text())["type"], "property_match")
        self.assertEqual(inventory.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
