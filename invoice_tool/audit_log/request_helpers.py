"""Legacy-compatible residual_audit helpers; explicit runtime dependencies."""

def request_ip_address(*, request):
    forwarded_for = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
    return forwarded_for or request.headers.get("X-Real-IP", "").strip() or request.remote_addr or ""
