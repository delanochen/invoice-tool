"""Legacy-compatible communication helpers; explicit runtime dependencies."""

def communication_languages_from_form(default="zh-CN", *, COMMUNICATION_LANGUAGES, request):
    """解析交流语言勾选，结果写入用户主数据 users 表两列。

    2026-09-19 起 UI 直接勾选语言（注册页/用户弹窗均无"首选"下拉）：
    勾选列表的第一项视为首选语言；仍兼容旧表单提交的
    preferred_communication_language 字段。一个都没勾时回退 default。
    """
    selected = [code for code in request.form.getlist("communication_language")
                if code in COMMUNICATION_LANGUAGES]
    preferred = request.form.get("preferred_communication_language", "").strip()
    if preferred not in COMMUNICATION_LANGUAGES:
        preferred = selected[0] if selected else (default if default in COMMUNICATION_LANGUAGES else "zh-CN")
    if preferred not in selected:
        selected.insert(0, preferred)
    return preferred, ",".join(dict.fromkeys(selected))
