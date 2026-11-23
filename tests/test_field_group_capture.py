"""Group capture: container check digits, vertical OCR, and confirm-then-watermark."""
import unittest
from io import BytesIO

from PIL import Image

from invoice_tool.service_reports.field_capture import (
    assess_container, container_candidates, fields_for_watermark, iso6346_check_digit,
    read_container_image, shifted_watermark, should_server_watermark, stamp_group_watermark, validate_container_llm,
    validate_position_llm, watermark_device_fields, watermark_lines, _watermark_rows,
)


class ContainerCheckDigitTest(unittest.TestCase):
    def test_known_iso_number_matches(self):
        self.assertEqual(iso6346_check_digit("CSQU305438"), "3")
        assessed = assess_container("CSQU 305438 3")
        self.assertEqual(assessed["container_number"], "CSQU3054383")
        self.assertTrue(assessed["check_digit_ok"])

    def test_bad_check_digit_is_kept_and_flagged(self):
        assessed = assess_container("CSQU3054380")
        self.assertEqual(assessed["container_number"], "CSQU3054380")
        self.assertFalse(assessed["check_digit_ok"])
        self.assertEqual(container_candidates("noise CSQU3054380 and CSQU3054383"), ["CSQU3054383", "CSQU3054380"])

    def test_llm_json_accepts_a_vertical_reading(self):
        found = validate_container_llm('{"container_number":"CSQU3054383"}')
        self.assertEqual(found["container_number"], "CSQU3054383")
        self.assertTrue(found["check_digit_ok"])
        self.assertIsNone(validate_container_llm('{"container_number":"NOT-A-CONTAINER"}'))
        self.assertEqual(validate_position_llm('{"position_number":"62-3"}')["position_number"], "62-3")
        self.assertEqual(validate_position_llm('{"position_number":"1A2"}')["position_number"], "1A2")

    def test_vertical_ocr_reads_the_rotated_column(self):
        def read_text(image):
            # The number is only legible after a quarter turn, when the strip is wide.
            return "CSQU3054383" if image.width > image.height else "||||"

        image = Image.new("RGB", (30, 180), "white")
        found = read_container_image(image, read_text)
        self.assertEqual(found["container_number"], "CSQU3054383")
        self.assertTrue(found["check_digit_ok"])

    def test_confirm_fields_are_what_the_watermark_uses(self):
        photo = {"equipment_number": "", "container_number": "", "position_number": ""}
        pending = {
            "equipment_number": "10232A0407536",
            "equipment_model": "LCI-400CR-01AZ-3487U",
            "container_number": "CSQU3054383",
            "position_number": "62-3",
            "fault": "P3 水泵干磨",
        }
        stamped = fields_for_watermark(photo, pending)
        self.assertTrue(stamped["watermark"])
        self.assertEqual(stamped["equipment_number"], "10232A0407536")
        self.assertEqual(stamped["container_number"], "CSQU3054383")
        raw = Image.new("RGB", (640, 480), "white")
        buffer = BytesIO()
        raw.save(buffer, format="JPEG")
        rendered = stamp_group_watermark(buffer.getvalue(), stamped)
        self.assertTrue(rendered.startswith(b"\xff\xd8"))
        self.assertGreater(len(rendered), 500)

    def test_saved_photo_is_watermarked_even_without_device_fields(self):
        lines = watermark_lines({
            "order_number": "SO-1",
            "technician_name": "张三",
            "when": "2026-11-17 18:00:00",
            "photo_type": "arrival",
        })
        self.assertIn("工单 SO-1", lines)
        self.assertIn("施工员 张三", lines)
        self.assertIn("类型 进场", lines)
        self.assertTrue(should_server_watermark("true", "abc", "abc"))
        self.assertTrue(should_server_watermark("false", "same", "same"))
        self.assertFalse(should_server_watermark("false", "watermarked", "raw", client_watermarked="true"))
        self.assertTrue(should_server_watermark("false", "raw", "different", client_watermarked="false", photo_role="container"))
        self.assertTrue(should_server_watermark("", "raw", "", client_watermarked="", photo_role="container"))
        self.assertFalse(should_server_watermark("true", "same", "same", client_watermarked="false", photo_role="container", watermark_opt_out="true"))
        self.assertTrue(should_server_watermark("true", "same", "same", watermark_opt_out="false"))
        raw = Image.new("RGB", (800, 600), "white")
        buffer = BytesIO()
        raw.save(buffer, format="JPEG")
        stamped = stamp_group_watermark(buffer.getvalue(), {
            "order_number": "SO-1",
            "technician_name": "张三",
            "when": "2026-11-17 18:00:00",
            "photo_type": "arrival",
        })
        self.assertTrue(stamped.startswith(b"\xff\xd8"))
        self.assertNotEqual(stamped, buffer.getvalue())
        self.assertTrue(shifted_watermark(
            "2026-11-17T18:00:37+00:00",
            "2026-11-17T18:00:37+00:00",
            "2026-11-17T17:00:00+00:00",
        ).startswith("2026-11-17T17:00:37"))
        later = shifted_watermark(
            "2026-11-17T18:02:10+00:00",
            "2026-11-17T18:00:37+00:00",
            "2026-11-17T19:00:00+00:00",
        )
        self.assertTrue(later.startswith("2026-11-17T19:02:10"))

    def test_aliases_and_repair_gaps_reach_the_watermark_card(self):
        posted = watermark_device_fields({
            "machine_number": "10232A0407536",
            "photo_model": "LCI-400CR-01AZ-3487U",
            "fault": "水泵干磨",
            "position_number": "b6-1",
            "container_number": "csqu3054383",
        })
        self.assertEqual(posted["equipment_number"], "10232A0407536")
        self.assertEqual(posted["equipment_model"], "LCI-400CR-01AZ-3487U")
        self.assertEqual(posted["fault_note"], "水泵干磨")
        self.assertEqual(posted["position_number"], "B6-1")
        filled = watermark_device_fields(
            {"equipment_number": "", "equipment_model": ""},
            {"equipment_number": "FROM-REPAIR", "equipment_model": "LCI-400", "fault": "已记录故障"},
        )
        self.assertEqual(filled["equipment_number"], "FROM-REPAIR")
        self.assertEqual(filled["equipment_model"], "LCI-400")
        self.assertEqual(filled["fault_note"], "已记录故障")
        # A typed number wins over a different value already stored on the photo.
        confirmed = fields_for_watermark(
            {"equipment_number": "STALE", "photo_model": "STALE-MODEL"},
            {"equipment_number": "10232A0407536", "equipment_model": "LCI-400CR-01AZ-3487U"},
        )
        self.assertEqual(confirmed["equipment_number"], "10232A0407536")
        self.assertEqual(confirmed["equipment_model"], "LCI-400CR-01AZ-3487U")
        rows = _watermark_rows({
            **posted,
            "photo_type": "equipment",
            "photo_type_label": "设备",
            "technician_name": "陈亦珹",
            "when": "2026-11-23T15:04:08+00:00",
            "timezone_name": "America/Chicago",
            "site_address": "9181 County Rd 196, Liverpool, TX 77577",
        })
        text = {label: value for label, value, _lines in rows}
        self.assertEqual(text["Machine No."], "10232A0407536")
        self.assertEqual(text["Model"], "LCI-400CR-01AZ-3487U")
        self.assertEqual(text["Location No."], "B6-1")
        self.assertEqual(text["Container No."], "CSQU3054383")
        self.assertEqual(text["Type"], "设备")
        self.assertEqual(text["Fault"], "水泵干磨")
        self.assertEqual(text["Technician"], "陈亦珹")


if __name__ == "__main__":
    unittest.main()
