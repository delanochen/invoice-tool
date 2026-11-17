"""Handheld-controller and nameplate text extracted for field repairs."""
import shutil
import unittest
from pathlib import Path

from invoice_tool.service_reports.field_repairs import (
    FAULT_TEXT_LIMIT, controller_fault_text, decode_ocr_output, nameplate_model, nameplate_numbers,
)
import field_work

ROOT = Path(__file__).resolve().parents[1]

CONTROLLER_SCREEN = """当前故障
2026/11/17 23:00:10 2级故障 故障码175
P3 水泵3干磨故障
2026/11/17 23:00:10 2级故障 故障码168
P2 水泵2干磨故障
page 1/2
"""

TONGFEI_PLATE = """Inverter Liquid Cooling Unit
Machine Type LCI-400CR-01AZ-3487U Refrigerant R134a
Machine Number 1023240407536 Manufacture Year 2024
"""


class FieldRepairRecognitionTest(unittest.TestCase):
    def test_controller_screen_keeps_simplified_chinese_fault_lines(self):
        text = controller_fault_text(CONTROLLER_SCREEN)
        self.assertEqual(text, "\n".join([
            "当前故障",
            "2026/11/17 23:00:10 2级故障 故障码175",
            "P3 水泵3干磨故障",
            "2026/11/17 23:00:10 2级故障 故障码168",
            "P2 水泵2干磨故障",
        ]))
        self.assertNotIn("page", text.casefold())
        self.assertLess(len(text), FAULT_TEXT_LIMIT)

    def test_spaced_chinese_ocr_is_joined_without_eating_the_pump_code(self):
        text = controller_fault_text("当 前 故 障\nP3 水 泵 3 干 磨 故 障\n")
        self.assertIn("当前故障", text)
        self.assertIn("P3 水泵3干磨故障", text)

    def test_fault_text_keeps_several_thousand_characters(self):
        long_text = "\n".join(
            f"2026/11/17 23:00:10 2级故障 故障码{index} 水泵{index}干磨故障" for index in range(200)
        )
        self.assertGreater(len(long_text), FAULT_TEXT_LIMIT)
        kept = controller_fault_text(long_text)
        self.assertEqual(len(kept), FAULT_TEXT_LIMIT)
        self.assertGreater(len(kept), 1000)
        self.assertTrue(kept.startswith("2026/11/17 23:00:10 2级故障 故障码0"))
        self.assertEqual(FAULT_TEXT_LIMIT, 4000)

    def test_utf8_chinese_is_not_decoded_as_latin1(self):
        raw = "水泵3干磨故障".encode("utf-8")
        self.assertEqual(decode_ocr_output(raw), "水泵3干磨故障")
        self.assertNotEqual(decode_ocr_output(raw), raw.decode("latin-1"))
        self.assertEqual(decode_ocr_output(b"\xef\xbb\xbf" + "故障".encode("utf-8")), "故障")

    def test_gb18030_fallback_when_bytes_are_not_utf8(self):
        raw = "水泵".encode("gb18030")
        self.assertEqual(decode_ocr_output(raw), "水泵")

    def test_tongfei_nameplate_reads_machine_type_and_ignores_the_next_label(self):
        # The previous pattern required the model to be the last token on the line,
        # so a two-column plate ("Machine Type … Refrigerant …") returned nothing.
        old = __import__("re").search(
            r"(?im)^\s*mach[i1l]ne[ \t]+type[ \t]*[:：]?[ \t]*(?:\r?\n[ \t]*)?([A-Z0-9][A-Z0-9._/\-]*)[ \t]*$",
            TONGFEI_PLATE,
        )
        self.assertIsNone(old)
        self.assertEqual(nameplate_model(TONGFEI_PLATE), "LCI-400CR-01AZ-3487U")

    def test_model_label_synonyms_and_separators(self):
        model = "LCI-400CR-01AZ-3487U"
        samples = [
            "Machine Type LCI-400CR-01AZ-3487U\nManufacture Year 2024\nMachine Number 1023231239085\n",
            "Machine Type = LCI-400CR-01AZ-3487U\n",
            "Machine Type\nLCI-400CR-01AZ-3487U\n",
            "Model: LCI-400CR-01AZ-3487U\n",
            "Model No. LCI-400CR-01AZ-3487U\n",
            "型号：LCI-400CR-01AZ-3487U\n",
            "设备型号\nLCI-400CR-01AZ-3487U\n",
            "Machlne Type LCI - 400CR - 01AZ - 3487U\n",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(nameplate_model(sample), model)
        self.assertEqual(nameplate_model("Machine Type Cooling\nManufacture Year 2024\n"), "")
        self.assertEqual(nameplate_model("Machine Number 1023240407536\n"), "")

    def test_refrigeration_is_not_a_machine_number(self):
        self.assertEqual(nameplate_numbers("Refrigeration\n1023240407536"), ["1023240407536"])
        self.assertEqual(nameplate_numbers("member2924040\neetONE5040936\n1023240407536"), ["1023240407536"])
        self.assertEqual(nameplate_numbers("1023240407536\nMachine Number\nSanhe Tongfei Refrigeration Co., Ltd."), ["1023240407536"])

    def test_mixed_number_beside_the_label_beats_letter_run_junk(self):
        self.assertEqual(
            nameplate_numbers("Machine Number 10232A0407536\nmember2924040\neetONE5040936"),
            ["10232A0407536"],
        )
        self.assertEqual(nameplate_numbers("机器编号\n10232A0407536"), ["10232A0407536"])
        self.assertEqual(
            nameplate_numbers("Machine Number 10232A0407536\n\n1023240407536"),
            ["10232A0407536", "1023240407536"],
        )
        self.assertEqual(
            nameplate_numbers("10232A0407536\n1023240407536"),
            ["1023240407536", "10232A0407536"],
        )

    def test_camera_frame_misreads_fit_the_tongfei_shape(self):
        self.assertEqual(nameplate_model("Machine Type\nLCI-400CR-014Z-3487U"), "LCI-400CR-01AZ-3487U")
        self.assertEqual(nameplate_model("LCI-AOOCR-01AZ-3487U\nMachine Type"), "LCI-400CR-01AZ-3487U")

    def test_invalid_vision_json_is_rejected_and_a_shaped_model_is_accepted(self):
        self.assertIsNone(field_work.validate_nameplate_llm("not json"))
        self.assertIsNone(field_work.validate_nameplate_llm({"machine_number": "Refrigeration", "equipment_model": "LCI-400CR-01AZ-3487U"}))
        self.assertIsNone(field_work.validate_nameplate_llm({"machine_number": "member2924040", "equipment_model": "LCI-400CR-01AZ-3487U"}))
        self.assertIsNone(field_work.validate_nameplate_llm({"machine_number": "102324040753", "equipment_model": "LCI-400CR-01AZ-3487U"}))
        mixed = field_work.validate_nameplate_llm({"machine_number": "10232A0407536", "equipment_model": "LCI-400CR-01AZ-3487U"})
        self.assertEqual(mixed["candidates"], ["10232A0407536"])
        self.assertEqual(mixed["equipment_model"], "LCI-400CR-01AZ-3487U")
        self.assertIsNone(field_work._vision_settings_for_nameplate({"vision_settings": lambda: {"enabled": False, "api_key": "", "model": ""}}))
        accepted = field_work.validate_nameplate_llm({"machine_number": "1023240407536", "equipment_model": "LCI-400CR-014Z-3487U"})
        self.assertEqual(accepted["source"], "llm")
        self.assertEqual(accepted["equipment_model"], "LCI-400CR-01AZ-3487U")
        self.assertEqual(accepted["candidates"], ["1023240407536"])

    def test_debug_frames_keep_thirty_and_drop_expired(self):
        import os
        import tempfile
        from datetime import datetime, timedelta, timezone
        with tempfile.TemporaryDirectory() as directory:
            field_work.record_nameplate_debug(directory, b"old-frame", {"source": "ocr", "text": "old"})
            old_path = next(Path(directory).glob("*.jpg"))
            expired = (datetime.now(timezone.utc) - timedelta(hours=49)).timestamp()
            os.utime(old_path, (expired, expired))
            os.utime(old_path.with_suffix(".json"), (expired, expired))
            for index in range(31):
                field_work.record_nameplate_debug(directory, f"frame-{index}".encode(), {"source": "ocr", "text": str(index)})
            kept = list(Path(directory).glob("*.jpg"))
            self.assertEqual(len(kept), 30)
            self.assertFalse(old_path.exists())
            self.assertIn(b'"source"', next(Path(directory).glob("*.json")).read_bytes())

    def test_real_tongfei_ocr_joins_the_model_printed_before_the_label(self):
        # PSM 11 of the phone-sized frame: the value is above "Machine Type" and split.
        phone = "LCI-400CR-O1AZ-\n3487U.\nMachine Type\n40.0kW/16.0kW\nMachine Number\n1023240407536\n"
        self.assertEqual(nameplate_model(phone), "LCI-400CR-01AZ-3487U")
        # Full-resolution frame: I->1, O->O, trailing U->0, and a space inside the code.
        full = "LC1-400CR-O1AZ- 34870.\n\nMachine Type\nCooling/Heating\n"
        self.assertEqual(nameplate_model(full), "LCI-400CR-01AZ-3487U")
        # The deployed 0.1.503 result was the short token "LCL-4000" when the suffix
        # was still present on the next line. The extra 0 is not kept.
        truncated = "Machine Type LCL-4000\nCR-01AZ-3487U\nCooling/Heating 40.0kW\n"
        self.assertEqual(nameplate_model(truncated), "LCI-400CR-01AZ-3487U")

    def test_field_page_accepts_chinese_and_raises_the_fault_limit(self):
        html = (ROOT / "templates" / "field_work.html").read_text(encoding="utf-8")
        tag = html.split('id="repairFault"', 1)[1].split(">", 1)[0]
        self.assertNotIn("maxlength", tag)
        self.assertIn('data-max-chars="4000"', tag)
        self.assertIn('lang="zh-CN"', tag)
        self.assertIn("维修前故障信息", html)
        script = (ROOT / "static" / "field-work.js").read_text(encoding="utf-8")
        self.assertIn("ImageCapture", script)
        self.assertIn("0.92", script)
        self.assertIn("compositionstart", script)
        self.assertIn("dataset.composing", script)
        self.assertNotIn("field.maxLength", script)
        self.assertIn("data-max-chars", html)
        backend = (ROOT / "field_work.py").read_text(encoding="utf-8")
        self.assertIn("chi_sim+eng", backend)
        self.assertIn("ensure_ascii=False", backend)
        self.assertIn("decode_ocr_output", backend)
        self.assertIn("value('fault', FAULT_TEXT_LIMIT)", backend)
        self.assertNotIn("value('fault', 1000)", backend)
        image = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("tesseract-ocr-chi-sim", image)
        worker = (ROOT / "static" / "field-sw.js").read_text(encoding="utf-8")
        self.assertIn("prasinos-field-68", worker)
        self.assertIn("正在识别铭牌，请稍候…", html)
        self.assertIn("ios-file-input", html)
        self.assertNotIn("$('faultAlbum').click()", script)
        self.assertNotIn("$('cameraAlbum').click()", script)
        self.assertNotIn("$('repairAlbum').click()", script)
        self.assertNotIn("$('nameplateAlbum').click()", script)
        for picker in ("faultAlbum", "nameplateAlbum", "cameraAlbum", "repairAlbum", "nameplateCamera"):
            tag = html.split(f'id="{picker}"', 1)[1].split(">", 1)[0]
            self.assertNotIn("hidden", tag)
            self.assertIn(".heic", tag)
        self.assertIn("从相册选择", html)
        self.assertIn('id="nameplateCamera"', html)
        self.assertIn('id="cameraAlbum"', html)
        self.assertIn('id="faultAlbum"', html)
        self.assertIn('id="repairAlbum"', html)
        self.assertNotIn("ocrTimer = setInterval", script)
        settings = (ROOT / "templates" / "company_settings.html").read_text(encoding="utf-8")
        self.assertIn("启用外部识图 API（铭牌/日报照片识图）", settings)
        self.assertIn("照片会发送到已配置的视觉模型服务商", settings)
        routes = (ROOT / "invoice_tool" / "app_settings" / "admin_routes.py").read_text(encoding="utf-8")
        self.assertIn('vision_external_api_enabled', routes)

    def test_real_tongfei_photo_returns_full_model_and_number(self):
        if shutil.which("tesseract") is None:
            self.skipTest("tesseract is not installed")
        import field_work
        photo = ROOT / "tests" / "fixtures" / "tongfei-lci-400cr-nameplate.jpg"
        result = field_work.recognize_nameplate_photo(photo.read_bytes())
        # Raw Tesseract reads I as 1, 0 as O, and U as 0, and puts the code
        # above the words "Machine Type". The API value is the corrected code.
        self.assertTrue("LC1-400CR-O1AZ-" in result["text"] or "LCI-400CR-O1AZ-" in result["text"])
        self.assertIn("Machine Type", result["text"])
        self.assertEqual(result["equipment_model"], "LCI-400CR-01AZ-3487U")
        self.assertEqual(result["candidates"][0], "1023240407536")

    def test_resized_fixture_keeps_model_and_number(self):
        if shutil.which("tesseract") is None:
            self.skipTest("tesseract is not installed")
        from io import BytesIO
        from PIL import Image
        photo = Image.open(ROOT / "tests" / "fixtures" / "tongfei-lci-400cr-nameplate.jpg")
        photo.load()
        for width in (1920, 1440, 1280, 1080):
            height = round(photo.height * width / photo.width)
            frame = photo.resize((width, height), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            frame.save(buffer, format="JPEG", quality=86)
            result = field_work.recognize_nameplate_photo(buffer.getvalue())
            with self.subTest(width=width):
                self.assertEqual(result["equipment_model"], "LCI-400CR-01AZ-3487U")
                self.assertEqual(result["candidates"][0], "1023240407536")

    def test_iphone_frames_keep_orientation_and_read_the_plate(self):
        if shutil.which("tesseract") is None:
            self.skipTest("tesseract is not installed")
        from PIL import Image
        names = [
            "nameplate-exif6-c311.jpg",
            "nameplate-exif6-7470.jpg",
            "nameplate-exif6-9c73.jpg",
            "nameplate-exif6-5dcb.jpg",
            "nameplate-exif6-0428.jpg",
        ]
        for name in names:
            photo = ROOT / "tests" / "fixtures" / name
            with self.subTest(name=name):
                with Image.open(photo) as opened:
                    self.assertEqual(opened.getexif().get(274), 6)
                result = field_work.recognize_nameplate_photo(photo.read_bytes())
                self.assertEqual(result["equipment_model"], "LCI-400CR-01AZ-3487U")
                self.assertEqual(result["candidates"][0], "1023240407536")


if __name__ == "__main__":
    unittest.main()
