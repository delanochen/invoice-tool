from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_test_compose_is_isolated_from_production():
    source = (ROOT / "deploy" / "docker-compose.test.yml").read_text(encoding="utf-8")
    assert "container_name: invoice-tool-test" in source
    assert '127.0.0.1:8089:8000' in source
    assert "/srv/invoice-tool-test/data:/app/data" in source
    assert "DEPLOYMENT_ENV: test" in source
    assert "cloudflared:" not in source
    assert "photo-worker:" not in source


def test_test_deployer_is_hard_limited_to_staging_and_invoice_test():
    source = (ROOT / "scripts" / "debian-test-deploy.sh").read_text(encoding="utf-8")
    assert 'BRANCH="${INVOICE_TEST_BRANCH:-staging}"' in source
    assert 'DATABASE="invoice_test"' in source
    assert "invoice_production" not in source
    assert "origin/main" not in source


def test_test_environment_has_visual_and_email_guards():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'app.config["DEPLOYMENT_ENV"]' in app
    assert '测试系统禁止发送真实邮件' in app
    assert "deployment_env == 'test'" in base
    assert "测试系统 · 数据和操作不得用于正式业务" in base
