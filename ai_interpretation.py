"""报销附件智能解读：调用本地大模型（OpenAI 兼容接口，如 Ollama）解读图片/PDF。

设计要点：
* 模型/接口配置全部来自 settings 表（系统设置页维护），代码不含兜底价或写死模型。
* 图片走多模态消息（base64 data URL）；PDF 先用 pypdf 提取文本再发给模型。
* 解读结果落库 expense_attachment_interpretations（一附件一行，unique(attachment_id)），
  「未解读过」= 无记录或 status='failed'。Web 端按钮与凌晨 2 点的批量 worker
  （ai_interpret_worker.py）共用本模块；建表由 app.init_db（SQLite）或
  migrations/postgresql/0281-*.sql（PostgreSQL）负责。
"""
import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_BASE_URL = os.environ.get("AI_INTERPRET_BASE_URL", "http://host.docker.internal:11434/v1")
TIMEOUT_SECONDS = max(30, int(os.environ.get("AI_INTERPRET_TIMEOUT_SECONDS", "300")))
MAX_PDF_CHARS = 12000

# 系统设置里可选的本地模型（model id 必须与 `ollama list` 中的名称一致，可在设置页修改）
MODEL_OPTIONS = (
    ("qwen4b", "Qwen 4B（本地）", "ai_interpret_model_qwen4b"),
    ("qwen9b", "Qwen 9B（本地）", "ai_interpret_model_qwen9b"),
)
MODEL_OPTION_KEYS = {key: (label, setting_key) for key, label, setting_key in MODEL_OPTIONS}

SETTINGS_DEFAULTS = {
    "ai_interpret_base_url": DEFAULT_BASE_URL,
    "ai_interpret_model_choice": "qwen4b",
    "ai_interpret_model_qwen4b": "qwen:4b",
    "ai_interpret_model_qwen9b": "qwen3.5:9b",
    "ai_interpret_model_custom": "",
    "ai_interpret_api_key": "",
}

PROMPT = (
    "你是财务助理。请解读这张报销附件，用简体中文输出要点：\n"
    "1. 文件类型（发票/收据/对账单/其他）与供应商名称；\n"
    "2. 日期与单据号（如有）；\n"
    "3. 币种与总金额，以及可识别的明细行（项目、数量、单价、小计）；\n"
    "4. 与报销审核相关的异常提示（金额不清、疑似重复、缺必要要素等；没有就写“未见明显异常”）。\n"
    "无法识别的内容直接说明无法识别，不要编造。"
)

SUPPORTED_IMAGE_TYPES = ("image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif")


def _now():
    return datetime.now(timezone.utc).isoformat()


def setting_value(connection, key):
    row = connection.execute("select value from settings where key = ?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else None


def effective_settings(connection):
    values = dict(SETTINGS_DEFAULTS)
    for key in SETTINGS_DEFAULTS:
        stored = setting_value(connection, key)
        if stored is not None and str(stored).strip() != "":
            values[key] = str(stored).strip()
    choice = values["ai_interpret_model_choice"]
    if choice == "custom":
        model = values["ai_interpret_model_custom"]
        label = "自定义模型"
    else:
        label, setting_key = MODEL_OPTION_KEYS.get(choice, MODEL_OPTIONS[0])
        model = values[setting_key]
    return {
        "base_url": values["ai_interpret_base_url"].rstrip("/"),
        "api_key": values["ai_interpret_api_key"],
        "model": model,
        "model_label": label,
    }


def call_chat_completion(settings, messages, timeout=TIMEOUT_SECONDS):
    """调用 OpenAI 兼容 /chat/completions，返回助手文本。"""
    if not settings["base_url"]:
        raise RuntimeError("未配置大模型接口地址，请先在系统设置中填写。")
    if not settings["model"]:
        raise RuntimeError("未配置大模型名称，请先在系统设置中选择或填写。")
    payload = json.dumps({"model": settings["model"], "messages": messages}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if settings["api_key"]:
        headers["Authorization"] = "Bearer " + settings["api_key"]
    request = urllib.request.Request(
        settings["base_url"] + "/chat/completions",
        data=payload,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = error.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"大模型接口返回 HTTP {error.code}：{detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"无法连接大模型接口（{settings['base_url']}）：{error.reason}") from error
    except (json.JSONDecodeError, ValueError) as error:
        raise RuntimeError("大模型接口返回了无法解析的内容。") from error
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("大模型接口响应缺少回复内容。") from error
    if not str(content or "").strip():
        raise RuntimeError("大模型返回了空回复。")
    return str(content)


def _attachment_file(connection, attachment_id, attachments_root):
    attachment = connection.execute(
        "select * from expense_attachments where id = ?", (attachment_id,)
    ).fetchone()
    if not attachment:
        raise RuntimeError("附件不存在。")
    path = os.path.join(str(attachments_root), str(attachment["expense_id"]), attachment["stored_filename"])
    if not os.path.isfile(path):
        raise RuntimeError("附件文件已不存在，无法解读。")
    return attachment, path


def _pdf_text(path):
    from pypdf import PdfReader

    reader = PdfReader(path)
    chunks = []
    total = 0
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        chunks.append(text)
        total += len(text)
        if total >= MAX_PDF_CHARS:
            break
    return "\n".join(chunks).strip()[:MAX_PDF_CHARS]


def _attachment_messages(attachment, path):
    content_type = (attachment["content_type"] or "").lower()
    filename = (attachment["original_filename"] or "").lower()
    if content_type in SUPPORTED_IMAGE_TYPES or (
        not content_type and filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
    ):
        with open(path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        mime = content_type or ("image/png" if filename.endswith(".png") else "image/jpeg")
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
                ],
            }
        ]
    if content_type == "application/pdf" or filename.endswith(".pdf"):
        text = _pdf_text(path)
        if not text:
            raise RuntimeError("PDF 内没有可提取的文本（可能是扫描件），当前本地模型无法解读图片型 PDF。")
        return [{"role": "user", "content": f"{PROMPT}\n\n以下是从 PDF 提取的文本：\n{text}"}]
    raise RuntimeError("只支持解读图片与 PDF 附件。")


def _save_result(connection, attachment_id, model, status, content, error):
    connection.execute(
        """
        insert into expense_attachment_interpretations
            (attachment_id, model, status, content, error, created_at, updated_at)
        values (?, ?, ?, ?, ?, ?, ?)
        on conflict(attachment_id) do update set
            model = excluded.model, status = excluded.status, content = excluded.content,
            error = excluded.error, updated_at = excluded.updated_at
        """,
        (attachment_id, model, status, content, error, _now(), _now()),
    )


def interpret_attachment(connection, attachment_id, attachments_root, force=False):
    """解读单个附件并落库；返回 {ok, status, content, model, error}。失败也记录（可重试）。"""
    try:
        attachment, path = _attachment_file(connection, attachment_id, attachments_root)
    except RuntimeError as error:
        return {"ok": False, "status": "failed", "content": "", "model": "", "error": str(error)}
    settings = effective_settings(connection)
    try:
        messages = _attachment_messages(attachment, path)
        content = call_chat_completion(settings, messages)
    except RuntimeError as error:
        message = str(error)
        _save_result(connection, attachment_id, settings["model"], "failed", "", message)
        return {"ok": False, "status": "failed", "content": "", "model": settings["model"], "error": message}
    _save_result(connection, attachment_id, settings["model"], "done", content, "")
    return {"ok": True, "status": "done", "content": content, "model": settings["model"], "error": ""}


def pending_attachment_ids(connection, limit=None):
    """未解读过（无记录或上次失败）的附件 id，按上传时间从旧到新。"""
    sql = """
        select ea.id
        from expense_attachments ea
        left join expense_attachment_interpretations x on x.attachment_id = ea.id
        where x.attachment_id is null or x.status = 'failed'
        order by ea.id
    """
    if limit:
        sql += f" limit {int(limit)}"
    return [row["id"] for row in connection.execute(sql).fetchall()]
