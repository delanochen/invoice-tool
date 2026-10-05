"""Focused coverage for employee-expense smart fill."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_interpretation
class ExpenseSmartFillTests(unittest.TestCase):
    def test_uses_attachment_interpret_scene_and_parses_json(self):
        payload = {
            "project_name": "Fuel", "amount": 42.35, "currency": "USD",
            "expense_date": "2026-10-04", "description": "加油收据",
            "is_fuel": True, "confidence": 0.96,
        }
        with patch.object(ai_interpretation, "effective_settings", return_value={"model": "vision"}) as settings, \
             patch.object(ai_interpretation, "_attachment_messages", return_value=[{"role": "user"}]), \
             patch.object(ai_interpretation, "call_chat_completion", return_value=json.dumps(payload)):
            result = ai_interpretation.analyze_expense_file(
                object(), "unused", "receipt.png", "image/png", ["Fuel", "Hotel"]
            )
        settings.assert_called_once()
        self.assertEqual(result["project_name"], "Fuel")
        self.assertTrue(result["is_fuel"])

    def test_invalid_model_json_requires_manual_intervention(self):
        with patch.object(ai_interpretation, "effective_settings", return_value={"model": "vision"}), \
             patch.object(ai_interpretation, "_attachment_messages", return_value=[]), \
             patch.object(ai_interpretation, "call_chat_completion", return_value="not json"):
            with self.assertRaisesRegex(RuntimeError, "人工干预"):
                ai_interpretation.analyze_expense_file(object(), "unused", "x.pdf", "application/pdf", ["Hotel"])

    def test_workflow_contains_required_controls(self):
        root = Path(__file__).parent
        module = (root / "expense_smart_fill.py").read_text(encoding="utf-8")
        template = (root / "templates" / "expense_smart_fill.html").read_text(encoding="utf-8")
        detail = (root / "templates" / "service_order_detail.html").read_text(encoding="utf-8")
        self.assertIn('"attachment_interpret"', (root / "ai_interpretation.py").read_text(encoding="utf-8"))
        self.assertIn("智能填报", detail)
        self.assertIn("生成一张报销单", template)
        self.assertIn("按类别分成多个报销单", template)
        self.assertIn("需人工干预", template)
        self.assertIn('{"personal", "rental"}', module)


if __name__ == "__main__":
    unittest.main()
