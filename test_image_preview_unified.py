"""Unified image preview contract tests (v0.1.260).

Background: the AI daily report draft detail page used to open the shared
#imageAttachmentPreviewDialog via `dialog.style.display = "flex"` (no
showModal), which rendered the dialog as a broken flex row (vertical title,
wrapped toolbar, image on the right) and left it impossible to close
(dialog.close() throws InvalidStateError when [open] is missing). The service
report form also had its own duplicate #nasPhotoPreviewDialog implementation.

Contract under test:
1.  AI daily report review JS must open previews through the standard opener
    (window.openAttachmentImagePreview -> showModal) and never display the
    dialog via an inline style.
2.  attachment-preview.js exposes window.openAttachmentImagePreview, closes
    safely (legacy inline-display tolerant), and maintains the zoom label.
3.  base.html dialog carries the unified toolbar order and zoom label.
4.  attachment-preview.css keeps the unified appearance (wide modal, dark
    image stage).
5.  service-report.js delegates the NAS photo preview to the shared dialog;
    the duplicate dialog, its zoom logic and its CSS are fully removed.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).parent


def read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


class AiDailyReportPreviewContract(unittest.TestCase):
    """AI 日报详情页必须走共享弹窗的标准开启路径。"""

    @classmethod
    def setUpClass(cls):
        cls.code = read("static", "ai-daily-report-review.js")

    def test_01_open_image_preview_uses_standard_opener(self):
        self.assertIn(
            "window.openAttachmentImagePreview === \"function\" && window.openAttachmentImagePreview(src)",
            self.code,
        )

    def test_02_no_inline_display_hack_in_preview_opener(self):
        # The legacy hack broke the modal layout and left it unclosable.
        # (Other overlays in this file are plain <div class="modal"> elements,
        # where inline display toggling is legitimate.)
        block = self.code.split("window.openImagePreview = function")[1].split("};")[0]
        self.assertNotIn("style.display", block)
        self.assertNotIn('dialog.style.display = "flex";\n    } else {', self.code)

    def test_03_fallback_opens_new_tab(self):
        # If the shared dialog is unavailable the fallback must still work.
        self.assertIn("window.open(src, \"_blank\")", self.code)


class SharedPreviewDialogContract(unittest.TestCase):
    """共享附件预览弹窗（base.html + attachment-preview.js/.css）。"""

    @classmethod
    def setUpClass(cls):
        cls.js = read("static", "attachment-preview.js")
        cls.css = read("static", "attachment-preview.css")
        cls.base = read("templates", "base.html")

    def test_01_public_entry_point_exists(self):
        self.assertIn("window.openAttachmentImagePreview = function (src, name)", self.js)

    def test_02_close_tolerates_legacy_inline_display(self):
        # close() throws InvalidStateError when the dialog was shown without
        # showModal(); the safe path clears the inline style instead.
        self.assertIn("if (imageAttachmentPreviewDialog?.open) {", self.js)
        self.assertIn('imageAttachmentPreviewDialog.style.display = "";', self.js)

    def test_03_open_clears_stale_inline_display(self):
        open_block = self.js.split("function openImageAttachmentPreview(")[1].split("window.openAttachmentImagePreview")[0]
        self.assertIn('imageAttachmentPreviewDialog.style.display = "";', open_block)
        self.assertIn("imageAttachmentPreviewDialog.showModal()", open_block)

    def test_04_zoom_level_label_maintained(self):
        self.assertIn('document.getElementById("imageAttachmentPreviewZoomLevel")', self.js)
        self.assertIn('imageAttachmentZoomLevel.textContent = imageAttachmentPreviewMode === "fit"', self.js)

    def test_05_backdrop_click_closes(self):
        self.assertIn("if (event.target === imageAttachmentPreviewDialog) closeImageAttachmentPreview();", self.js)

    def test_06_base_dialog_toolbar_unified(self):
        head = self.base.split('id="imageAttachmentPreviewDialog"')[1].split("</dialog>")[0]
        # Unified button order: 缩小 放大 自适应 原图 [zoom%] 关闭 (matches the
        # former NAS preview toolbar).
        self.assertLess(head.index("data-image-preview-out"), head.index("data-image-preview-in"))
        self.assertLess(head.index("data-image-preview-in"), head.index("data-image-preview-fit"))
        self.assertLess(head.index("data-image-preview-fit"), head.index("data-image-preview-original"))
        self.assertLess(head.index("data-image-preview-original"), head.index("imageAttachmentPreviewZoomLevel"))
        self.assertLess(head.index("imageAttachmentPreviewZoomLevel"), head.index("data-image-preview-close"))
        self.assertIn('id="imageAttachmentPreviewZoomLevel"', head)

    def test_07_base_loads_preview_assets(self):
        self.assertIn("attachment-preview.css", self.base)
        self.assertIn("attachment-preview.js", self.base)

    def test_08_css_keeps_unified_appearance(self):
        # Wide modal + dark stage, matching the former NAS preview dialog.
        self.assertIn(".modal.image-attachment-preview-modal{width:min(1180px,calc(100% - 20px))", self.css)
        self.assertIn("background:#0f172a", self.css)
        self.assertIn("height:min(78vh,760px)", self.css)
        self.assertIn("attachment-image-viewport", self.css)


class ServiceReportPreviewDelegationContract(unittest.TestCase):
    """工单日报 NAS 照片预览并入共享弹窗，重复实现全部移除。"""

    @classmethod
    def setUpClass(cls):
        cls.js = read("static", "service-report.js")
        cls.html = read("templates", "service_report_form.html")
        cls.css = read("static", "styles.css")

    def test_01_open_nas_photo_preview_delegates(self):
        block = self.js.split("function openNasPhotoPreview(")[1].split("function ")[0]
        self.assertIn("window.openAttachmentImagePreview(src", block)
        # thumbnail fallback and image name passthrough
        self.assertIn("image.preview || image.thumbnail", block)

    def test_02_duplicate_dialog_markup_removed(self):
        self.assertNotIn("nasPhotoPreviewDialog", self.html)
        self.assertNotIn("nas-photo-preview", self.html)

    def test_03_duplicate_js_removed(self):
        for banned in (
            "nasPhotoPreviewDialog",
            "nasPhotoPreviewImage",
            "nasPhotoPreviewTitle",
            "nasPhotoZoomLevel",
            "applyNasPhotoZoom",
            "setNasPhotoZoom",
            "resetNasPhotoZoom",
            "showNasPhotoOriginalSize",
            "closeNasPhotoPreview",
            "nasPhotoOriginalSize",
        ):
            self.assertNotIn(banned, self.js)

    def test_04_duplicate_css_removed(self):
        self.assertNotIn(".nas-photo-preview-dialog", self.css)
        self.assertNotIn(".nas-photo-preview-wrap", self.css)
        self.assertNotIn(".nas-photo-preview-actions", self.css)

    def test_05_callers_still_wired(self):
        # Definition + two call sites: NAS browser grid and selected-photo thumbs.
        self.assertEqual(self.js.count("openNasPhotoPreview(image)"), 3, "定义 + 两个调用点（浏览器网格/已选照片）都应保留")


if __name__ == "__main__":
    unittest.main()
