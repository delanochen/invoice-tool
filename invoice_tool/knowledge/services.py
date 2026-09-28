"""Knowledge-document storage and lookup behavior.

The service intentionally preserves the behavior of the former ``app.py``
helpers. Route authorization, transactions, flash messages, and redirects stay
in the route layer.
"""

import os
import secrets

from flask import abort
from pypdf import PdfReader


class KnowledgeService:
    def __init__(self, *, db, storage_dir, max_pdf_bytes, max_extracted_text):
        self.db = db
        self.storage_dir = storage_dir
        self.max_pdf_bytes = max_pdf_bytes
        self.max_extracted_text = max_extracted_text

    def extract_pdf_text(self, pdf_path):
        try:
            reader = PdfReader(pdf_path, strict=False)
            text_parts = []
            text_length = 0
            for page in reader.pages:
                page_text = (page.extract_text() or "").strip()
                if not page_text:
                    continue
                remaining = self.max_extracted_text - text_length
                if remaining <= 0:
                    break
                page_text = page_text[:remaining]
                text_parts.append(page_text)
                text_length += len(page_text) + 1
            return "\n".join(text_parts)[:self.max_extracted_text]
        except Exception:
            return ""

    def save_pdf_upload(self, uploaded):
        if not uploaded or not uploaded.filename:
            raise ValueError("请选择要上传的 PDF 文档。")
        original_filename = os.path.basename(uploaded.filename).strip()
        if not original_filename.lower().endswith(".pdf"):
            raise ValueError("知识库目前仅支持 PDF 文档。")
        header = uploaded.stream.read(5)
        uploaded.stream.seek(0)
        if header != b"%PDF-":
            raise ValueError("文件内容不是有效的 PDF 文档。")
        stored_filename = f"{secrets.token_hex(16)}.pdf"
        os.makedirs(self.storage_dir, exist_ok=True)
        destination = os.path.join(self.storage_dir, stored_filename)
        try:
            uploaded.save(destination)
            file_size = os.path.getsize(destination)
            if file_size > self.max_pdf_bytes:
                raise ValueError("PDF 文档不能超过 50 MB。")
            return {
                "original_filename": original_filename,
                "stored_filename": stored_filename,
                "destination": destination,
                "file_size": file_size,
                "extracted_text": self.extract_pdf_text(destination),
            }
        except Exception:
            try:
                os.remove(destination)
            except FileNotFoundError:
                pass
            raise

    def document_path(self, document):
        return os.path.join(self.storage_dir, document["stored_filename"])

    def version_path(self, version):
        return os.path.join(self.storage_dir, version["stored_filename"])

    def document_or_404(self, document_id):
        document = self.db().execute(
            "select * from knowledge_documents where id = ?",
            (document_id,),
        ).fetchone()
        if not document:
            abort(404)
        return document

    def version_or_404(self, version_id):
        version = self.db().execute(
            "select * from knowledge_document_versions where id = ?",
            (version_id,),
        ).fetchone()
        if not version:
            abort(404)
        return version
