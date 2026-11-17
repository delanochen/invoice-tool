"""Group capture: container check digits, vertical OCR, and confirm-then-watermark."""
import unittest
from io import BytesIO

from PIL import Image

from invoice_tool.service_reports.field_capture import (
    assess_container, container_candidates, fields_for_watermark, iso6346_check_digit,
    read_container_image, stamp_group_watermark, validate_container_llm, validate_position_llm,
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


if __name__ == "__main__":
    unittest.main()
