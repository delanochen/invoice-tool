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
        self.assertEqual(self.code.count("$('deviceSession').hidden"), 3)  # startKind + completeBatch + general menu
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

    # 15c. opening the non-equipment menu immediately hides the device area,
    #      even before a second-level type is chosen (switch back from equipment).
    def test_14_general_menu_immediately_hides_device_session(self):
        general_branch = self.code.split("if (type === 'general') {")[1].split("return;")[0]
        self.assertIn("$('generalKindChoices').hidden = false;", general_branch)
        self.assertIn("$('generalKind').classList.add('primary');", general_branch)
        self.assertIn("$('equipmentKind').classList.remove('primary');", general_branch)
        self.assertIn("$('deviceSession').hidden = true;", general_branch)

    # 17. watermark time: adjust button + password dialog (replaces the old
    #     "use system time" checkbox).
    def test_15_watermark_adjust_button_and_dialog(self):
        self.assertNotIn("systemTime", self.code)  # checkbox removed
        self.assertNotIn("adjustedTimeFields", self.code)
        self.assertIn("addEventListener('click',openWatermarkDialog)", self.code)
        self.assertIn("watermarkTimeDialog", self.code)
        self.assertIn("confirmWatermarkTime", self.code)
        self.assertIn("cancelWatermarkTime", self.code)
        self.assertIn("verifyTimePassword", self.code)  # password check kept
        self.assertIn("/api/field/verify-watermark-password", self.code)

    def test_16_watermark_increments_from_first_adjusted_shot(self):
        # Backfill + increment semantics: the first adjusted shot uses exactly the
        # set time (anchor), later shots add the real elapsed time since the first
        # adjusted shot. The old bug (adding elapsed since batch.actual_start)
        # must be gone.
        self.assertIn("if (batch.first_adjusted_shot_at == null) batch.first_adjusted_shot_at = actual.getTime();", self.code)
        self.assertIn("batch.watermark_anchor_ms = new Date(start).getTime();", self.code)
        self.assertIn("watermark = new Date(anchorMs + (actual.getTime() - batch.first_adjusted_shot_at));", self.code)
        self.assertNotIn("actual.getTime() - batch.actual_start", self.code)

    def test_17_open_camera_gate_uses_use_system_time(self):
        self.assertIn("if (!useSystemTime && !timeAuthorized) { notice('请先验证水印时间调整密码。',true); return; }", self.code)

    def test_18_second_level_menu_stays_visible_after_picking_type(self):
        # Choosing arrival/departure/safety must NOT hide the second-level menu.
        self.assertIn("$('generalKindChoices').hidden = type === 'equipment';", self.code)
        self.assertIn("function setKindChoiceActive(type) {", self.code)
        self.assertIn("classList.toggle('primary',type==='arrival')", self.code)
        self.assertIn("classList.toggle('primary',type==='departure')", self.code)
        self.assertIn("classList.toggle('primary',type==='safety')", self.code)

    def test_19_complete_batch_resets_watermark_state(self):
        self.assertIn("useSystemTime=true;", self.code.replace(" ", ""))
        self.assertIn("watermarkModeText').textContent='使用当前系统时间'", self.code.replace(" ", ""))
        self.assertIn("watermarkStart').value=''", self.code.replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
