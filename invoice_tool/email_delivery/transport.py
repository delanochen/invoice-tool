"""Legacy-compatible smtp_transport helpers; explicit runtime dependencies."""

def send_email(to, subject, html, attachments=None, *, EmailMessage, app, get_smtp_settings, smtplib):
    if app.config.get("DEPLOYMENT_ENV") == "test":
        raise RuntimeError("测试系统禁止发送真实邮件。")
    smtp_settings = get_smtp_settings()
    host = smtp_settings["host"].strip()
    if not host:
        raise RuntimeError("公司设置中的 SMTP Host 未配置。")
    user = smtp_settings["user"].strip()
    password = smtp_settings["password"]
    if user and not password:
        raise RuntimeError("公司设置中的 SMTP Password 未配置。Gmail 需要使用应用专用密码，不是普通登录密码。")
    message = EmailMessage()
    message["From"] = smtp_settings["from"] or user or "billing@example.com"
    message["To"] = to
    message["Subject"] = subject
    message.set_content("Please see the attached documents.")
    message.add_alternative(html, subtype="html")
    for attachment in attachments or []:
        message.add_attachment(
            attachment["content"],
            maintype=attachment["maintype"],
            subtype=attachment["subtype"],
            filename=attachment["filename"],
        )
    port = int(smtp_settings["port"] or "587")
    use_tls = smtp_settings["tls"].lower() == "true"
    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if use_tls:
                smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as error:
        raise RuntimeError("Gmail 登录失败。请确认公司设置中的 SMTP Password 是 Gmail 应用专用密码，并且账号已开启两步验证。") from error
    except (smtplib.SMTPException, OSError, ValueError) as error:
        raise RuntimeError(f"邮件发送失败：{error}") from error
