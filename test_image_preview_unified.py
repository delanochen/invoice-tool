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


class SuspiciousAttachmentPreviewContract(unittest.TestCase):
    """报销审核「重复附件检查」里的疑点附件：必须走共享预览弹窗，而不是新开标签页。

    原来两个链接都写死 target="_blank"：点一下整页跳走、看完还得按返回 —— 而审核员本来
    就是要「把可疑附件和命中记录对着比一比」，来回跳页把这个动作切碎了。
    """

    @classmethod
    def setUpClass(cls):
        cls.html = read("templates", "expense_detail.html")
        cls.app = read("app.py")

    def test_01_both_cells_go_through_the_shared_macro(self):
        # 「当前附件」与「匹配记录」两列都走 suspect_attachment()，宏内部按 is_image_attachment
        # 分流；只要有人在行内重新写 target="_blank"，图片附件就又变成新开页。
        self.assertEqual(self.html.count("{{ suspect_attachment("), 2)
        self.assertIn("{% macro suspect_attachment(href, name, content_type, extra_class='') %}", self.html)
        block = self.html.split("{% macro suspect_attachment(")[1].split("{% endmacro %}")[0]
        self.assertIn("is_image_attachment(content_type, name)", block)
        self.assertIn("data-image-preview", block)
        self.assertIn('target="_blank"', block)
        self.assertIn("inline-thumb", block)      # 图片旁边给缩略图，和「报销明细」一个样子

    def test_02_query_carries_attachment_content_type(self):
        # 没有 content_type 就无法判断该弹窗还是该新开页（回到按扩展名猜的脆弱老路）
        query = self.app.split("def expense_duplicate_checks(")[1].split(").fetchall()")[0]
        self.assertIn("current_attachment.content_type as attachment_content_type", query)
        self.assertIn("matched_attachment.content_type as matched_attachment_content_type", query)


class MileageUploadRowContract(unittest.TestCase):
    """日报「里程佐证」：文件选择控件与「自动生成里程佐证」按钮要同一行、同一风格。

    背景：ui-i18n.js 在 `language === 'zh-CN'` 时**直接 return**（整段翻译与「替换原生控件」
    的逻辑都不跑），所以中文用户看到的就是浏览器原生控件。原来它与那个按钮都是 .form-field
    （display:grid）的直接子元素，于是各自拉满一整行 —— 一个原生小盒子 + 一个全宽系统按钮，
    两种风格并列（用户报过「这两个风格不一致」）。
    """

    @classmethod
    def setUpClass(cls):
        cls.html = read("templates", "service_report_form.html")
        cls.css = read("static", "styles.css")
        cls.pending = read("static", "pending-attachments.js")

    def test_01_picker_and_generate_button_share_a_row(self):
        block = self.html.split('class="file-picker-row"')[1].split("</div>")[0]
        self.assertIn('name="mileage_proof_attachments"', block)
        self.assertIn('id="generateMileageEvidenceBtn"', block)
        self.assertIn('id="mileageEvidenceStatus"', block)

    def test_02_wrapper_is_the_anchor_pending_attachments_expects(self):
        # pending-attachments.js 用 input.closest("label, .file-picker-row") 当插入锚点：
        # 换掉这个类，「待上传附件」预览面板会被插进行内，把这一行撑坏。
        self.assertIn('input.closest("label, .file-picker-row")', self.pending)

    def test_03_native_file_button_restyled_like_system_button(self):
        # 中文环境下没人替我们替换控件，外观只能靠这条伪元素规则；尺寸对齐 .small 按钮
        block = self.css.split('input[type="file"]::file-selector-button {')[1].split("}")[0]
        self.assertIn("min-height: 32px", block)
        self.assertIn("padding: 0 10px", block)
        self.assertIn("border: 1px solid var(--line)", block)
        self.assertIn("border-radius: 6px", block)
        self.assertIn("font-size: 13px", block)

    def test_04_already_hidden_pickers_keep_winning(self):
        """已经用 opacity/尺寸把原生控件藏起来的包装器必须继续胜出。

        `.photo-local-picker input[type=file]` 与基础规则 `input[type="file"]` 同优先级
        （都是 0,1,1），靠**文档顺序**决胜负 —— 隐藏规则必须排在后面。
        （移动打卡页的 .camera-button 是独立页面：只加载 mobile-clock-in.css、不引 styles.css，
        所以本次改动碰不到它。）
        """
        self.assertLess(
            self.css.index('input[type="file"] {'),
            self.css.index(".photo-local-picker input[type=file]"),
            "隐藏规则必须排在基础规则之后，否则原生控件会重新冒出来",
        )
        self.assertIn(".native-photo-picker input[type=file]", read("static", "field-work.css"))
        clock_in = read("templates", "mobile_clock_in.html")
        self.assertIn("mobile-clock-in.css", clock_in)
        self.assertNotIn("styles.css", clock_in)


if __name__ == "__main__":
    unittest.main()
