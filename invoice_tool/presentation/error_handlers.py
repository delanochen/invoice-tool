"""Legacy-compatible residual_errors helpers; explicit runtime dependencies."""

def runtime_error(error, *, flash, redirect, request, url_for):
    flash(str(error), "error")
    return redirect(request.referrer or url_for("dashboard"))


def forbidden(error, *, is_external_user, render_template):
    if is_external_user():
        message = "你的外部账号只能查看与自己所属客户相关的工单和工作日报，这张工单不在你的可见范围内。如需访问，请联系内部管理员开通权限。"
    else:
        message = "你没有权限访问这个页面或记录。如需访问，请联系管理员。"
    return (
        render_template(
            "error.html",
            status_code="403",
            title="没有访问权限",
            message=message,
        ),
        403,
    )


def not_found(error, *, app, render_template, request):
    app.logger.warning(
        "404 response: method=%s path=%s endpoint=%s description=%s",
        request.method,
        request.path,
        request.endpoint,
        getattr(error, "description", ""),
    )
    return (
        render_template(
            "error.html",
            status_code="404",
            title="没有找到这个页面或记录",
            message="你输入的地址可能不正确，或者这张发票、附件、客户资料已经被删除。",
        ),
        404,
    )


def internal_server_error(error, *, app, render_template):
    """兜底：任何未捕获异常都显示中文提示页，而不是 Werkzeug 的英文 500 页。

    真实堆栈只进日志（便于排查），页面只给可操作提示。数据是事务性的，
    出错路径都已 rollback，这里明确告知用户「数据没有被改动」。
    """
    app.logger.exception("未处理的服务器错误：%s", error)
    return (
        render_template(
            "error.html",
            status_code="500",
            title="服务器出了点问题",
            message="这个操作没能完成，数据没有被改动。请返回上一页重试；如果反复出现，请把当前页面地址和你做的操作发给管理员。",
        ),
        500,
    )


def request_entity_too_large(error, *, app, render_template, request):
    app.logger.warning("413 caught: error=%r content_length=%s max_content_length=%s path=%s environ=%s",
        error, request.content_length, request.max_content_length, request.path,
        {k:v for k,v in request.environ.items() if 'CONTENT' in k.upper() or 'MAX' in k.upper()})
    return (
        render_template(
            "error.html",
            status_code="413",
            title="提交内容过大",
            message="提交的数据超过了服务器限制。请减少同时编辑的行数或稍后重试。",
        ),
        413,
    )
