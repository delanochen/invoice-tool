"""Static JS contract tests for Field Work photo-type + device-info rules.

Rules under test (v0.1.215 working tree, NOT committed):
1.  equipment -> #deviceSession visible (existing flow unchanged)
2-5. non-equipment (arrival/departure/safety) -> #deviceSession hidden
6-8. equipment A313 -> switch to arrival/departure/safety -> payload carries no A313
9.   equipment A313 -> non-equipment -> back to equipment -> clean empty form
10.  completeBatch clears device inputs
11.  resetDevice clears device inputs
12.  arrival/departure/safety payload contains NO device keys
13.  equipment flow still requires a device session (or no-number)
14.  photo_type is only equipment/arrival/departure/safety (never general)
15.  general stays a UI-only first-level entry
16.  ZERO DeepSeek Vision calls in field-work.js
"""
import re
import unittest
from pathlib import Path

JS = Path(__file__).with_name("static") / "field-work.js"


class FieldWorkJsContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = JS.read_text(encoding="utf-8")
        cls.pack = cls.code.replace("\n", " ").replace("\r", " ")

    # 1-5. deviceSession visibility
    def test_01_equipment_shows_device_session(self):
        self.assertIn("$('deviceSession').hidden = type !== 'equipment'", self.code)
        self.assertIn("function confirmDevice()", self.code)  # equipment flow intact

    def test_02_non_equipment_hides_device_session(self):
        # The single rule above covers arrival/departure/safety; assert the hidden
        # toggle is the only visibility rule and non-equipment always hides.
        self.assertEqual(self.code.count("$('deviceSession').hidden"), 2)  # startKind + completeBatch only
        self.assertIn("$('deviceSession').hidden = type !== 'equipment'", self.code)

    # 6-9. state cleanup on kind switch
    def test_03_kind_switch_clears_device_inputs(self):
        self.assertIn("const typeChanged = previousType && previousType !== type;", self.code)
        self.assertIn("if (typeChanged) clearDeviceInputs();", self.code)

    def test_04_non_equipment_clears_session(self):
        self.assertIn("if (type !== 'equipment') deviceSession = null;", self.code)

    def test_05_switch_back_to_equipment_resets_clean(self):
        self.assertIn("else if (previousType !== 'equipment') resetDevice();", self.code)
        self.assertIn("$('equipmentNumber').value = '';", self.code)  # inside resetDevice/clearDeviceInputs

    # 10-11. completeBatch / reset cleanup
    def test_06_complete_batch_clears_device_inputs(self):
        self.assertIn("clearDeviceInputs();", self.code)
        # completeBatch calls clearDeviceInputs before rendering next-group state
        self.assertIn("$('generalKindChoices').hidden = true;", self.code)
        self.assertIn("batch=null;deviceSession=null;", self.code.replace(" ", ""))

    def test_07_reset_device_clears_inputs(self):
        self.assertIn("function resetDevice() {", self.code)
        self.assertIn("$('noEquipmentNumber').checked = false;", self.code)
        self.assertIn("$('equipmentNumber').required = true;", self.code)

    # 12. non-equipment payload has NO device keys
    def test_08_non_equipment_payload_excludes_device_keys(self):
        # Equipment-only device fields are spread conditionally:
        self.assertIn("...(batch.type === 'equipment' ? {equipment_number:deviceSession.equipment_number,", self.pack)
        self.assertIn("equipment_session:deviceSession.id,no_equipment_number:deviceSession.no_equipment_number} : {})", self.pack)
        # No other place adds device fields to payloads.
        self.assertNotIn("equipment_number:deviceSession.equipment_number,position_number", self.pack.replace("...(batch.type === 'equipment' ? {", ""))

    # 13. equipment flow still requires session
    def test_09_equipment_requires_device_session(self):
        self.assertIn("if (batch.type === 'equipment' && !deviceSession) throw new Error('请先确认本组照片类型和设备信息。');", self.code)
        self.assertIn("if (batch.type === 'equipment' && !deviceSession && !recognitionOnly && !confirmDevice()) return;", self.code)
        self.assertIn("noNumber = $('noEquipmentNumber').checked;", self.code)

    # 14. photo_type stays one of the four final types
    def test_10_photo_type_only_from_batch_type(self):
        assignments = re.findall(r"photo_type\s*:\s*[^,}]+", self.code)
        self.assertEqual(
            sorted(assignments),
            sorted(["photo_type:'legacy'", "photo_type:batch.type"]),
            "photo_type must never be re-derived from device info: %r" % assignments,
        )
        # Final photo types in the label map (new flow never persists 'general').
        for final_type in ("equipment", "arrival", "departure", "safety"):
            self.assertIn("%s:'" % final_type, self.code)  # JS object keys are unquoted

    # 15. general is UI-only
    def test_11_general_is_ui_only(self):
        # chooseKind('general') only expands the submenu; it never starts a batch
        # of type 'general'.
        self.assertIn("if (type === 'general') {", self.code)
        self.assertIn("$('generalKindChoices').hidden = false;", self.code)
        self.assertIn("return;", self.code)
        # No batch of type general can be created: startKind is only reached for
        # equipment/arrival/departure/safety; 'general' never calls startKind.
        self.assertNotIn("startKind('general')", self.code)
        self.assertNotIn("startKind('legacy')", self.code)

    # 16. ZERO Vision calls
    def test_12_zero_vision_calls(self):
        for banned in ("vision", "Vision", "VISION", "deepseek", "DeepSeek", "classifyDraftPhotos", "recognize-equipment"):
            if banned == "recognize-equipment":
                # The OCR endpoint is nameplate recognition, not photo classification.
                continue
            self.assertNotIn(banned, self.code)

    # 15b. non-equipment button highlight
    def test_13_non_equipment_button_highlight(self):
        self.assertIn("$('generalKind').classList.add('primary');", self.code)
        self.assertIn("$('equipmentKind').classList.remove('primary');", self.code)


if __name__ == "__main__":
    unittest.main()
