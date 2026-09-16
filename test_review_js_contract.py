"""Static JS contract tests for the AI Daily Report review page.

The review script is wrapped in an IIFE, so any function referenced from
inline handlers (onclick=...) must be explicitly exported to window, or the
browser raises ReferenceError at click time. These tests scan the generated
HTML templates inside the JS and assert every inline handler target is
globally resolvable, preventing the openPhotoDialog scope regression from
recurring. No browser test framework is required.
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent
JS_PATH = ROOT / "static" / "ai-daily-report-review.js"

INLINE_HANDLER_RE = re.compile(r'\bon(?:click|change|input|submit|keydown)="([^"]+)"')


class ReviewJsContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = JS_PATH.read_text(encoding="utf-8")
        # Strip block and line comments so only executable code is inspected.
        stripped = re.sub(r"/\*.*?\*/", "", cls.src, flags=re.S)
        cls.code = re.sub(r"//[^\n]*", "", stripped)

    def test_01_openPhotoDialog_exported_for_inline_onclick(self):
        if 'onclick="openPhotoDialog(' in self.code:
            self.assertIn(
                "window.openPhotoDialog = openPhotoDialog",
                self.code,
                "openPhotoDialog is used by an inline onclick but never exported "
                "to window (ReferenceError in browser).",
            )

    def test_02_every_inline_handler_target_is_globally_resolvable(self):
        handlers = INLINE_HANDLER_RE.findall(self.code)
        self.assertTrue(handlers, "expected inline handlers in review JS")
        unresolved = []
        for handler in handlers:
            expr = handler.strip()
            if expr.startswith("window.") or expr.startswith("document."):
                continue
            match = re.match(r"([A-Za-z_$][\w$]*)", expr)
            if not match:
                continue
            name = match.group(1)
            if name in ("window", "document", "event"):
                continue
            # The bare identifier must be exported as window.<name> = <name>.
            if ("window." + name + " = " + name) not in self.code:
                unresolved.append(handler)
        self.assertEqual(
            unresolved,
            [],
            "inline handlers reference identifiers that are not exported to "
            "window: %r" % unresolved,
        )

    def test_03_legacy_pickPhoto_inline_handler_removed(self):
        self.assertNotIn(
            'onclick="pickPhoto(',
            self.code,
            "legacy pickPhoto inline handler must not reappear in the new flow",
        )

    def test_04_photo_dialog_functions_use_event_listeners_not_inline(self):
        # markPhoto / savePhotoTime / autoSelectPhotos are wired via
        # addEventListener inside the dialog; they must NOT be referenced from
        # inline handlers (which would require global exports).
        for name in ("markPhoto", "savePhotoTime", "autoSelectPhotos"):
            self.assertNotIn(
                'onclick="' + name + "(",
                self.code,
                "%s is called inline but wired with addEventListener" % name,
            )


if __name__ == "__main__":
    unittest.main()
