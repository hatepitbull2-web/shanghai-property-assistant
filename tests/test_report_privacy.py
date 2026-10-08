"""Regression checks for client report privacy and faithful uncertainty display."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import assistant
import render_reports
import render_viewing
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


class ClientReportPrivacyTests(unittest.TestCase):
    def fixture(self):
        data = json.loads((Path(__file__).parent / 'fixtures/report_privacy.json').read_text())
        return data['inventory'], data['customer']

    def test_raw_customer_and_project_never_enter_model(self):
        inventory, customer = self.fixture()
        matched = assistant.match_inventory(inventory, customer)
        model = render_reports.build_model(inventory, customer, matched)
        self.assertNotIn('QA_PRIVATE', json.dumps(model, ensure_ascii=False))

    def test_budget_conflict_and_extra_condition_remain_visible(self):
        inventory, customer = self.fixture()
        matched = assistant.match_inventory(inventory, customer)
        model = render_reports.build_model(inventory, customer, matched)
        visible = '\n'.join(e.get('text', '') for slide in model['slides'] for e in slide['elements'])
        self.assertIn('1100-1400', visible)
        self.assertIn('700-900', visible)
        self.assertIn('预算检查：待核实', visible)
        self.assertNotIn('展示总价范围在预算内', visible)
        self.assertIn('commute_max_minutes', visible)
        for check in matched['recommendations'][0]['checks'].values():
            self.assertIn(check['reason'], visible)

    @unittest.skipIf(PdfReader is None, "Install pypdf to verify generated PDF text")
    def test_arbitrary_viewing_preference_keys_are_private(self):
        data = assistant.viewing_report(json.loads((ROOT / 'examples/viewing.json').read_text()))
        data['preference_updates']['内部佣金'] = ['QA_PRIVATE_VIEWING_COMMISSION']
        data['preference_updates']['内部嵌套'] = {'floor': 'QA_PRIVATE_VIEWING_FLOOR'}
        data['经纪人内部备注'] = 'QA_PRIVATE_TOP'
        data['projects'][0]['内部佣金'] = 'QA_PRIVATE_PROJECT'
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'report.pdf'
            render_viewing.render_viewing(data, target)
            text = ''.join(page.extract_text() or '' for page in PdfReader(target).pages)
        self.assertNotIn('QA_PRIVATE', text)
        self.assertNotIn('内部佣金', text)
        self.assertIn(data['preference_updates']['confirmed'][0], text)

    def test_display_fields_do_not_stringify_arbitrary_objects(self):
        self.assertEqual(render_reports.clean({'内部佣金': 'QA_PRIVATE'}), '待核实')
        self.assertEqual(render_reports.clean([{'内部佣金': 'QA_PRIVATE'}]), '待核实')


if __name__ == '__main__':
    unittest.main()
