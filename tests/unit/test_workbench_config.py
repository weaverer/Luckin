from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr, ValidationError

from lucking.config import Settings


def test_workbench_security_defaults_are_server_only() -> None:
    settings = Settings(_env_file=None)

    assert settings.session_cookie_name == "lucking_session"
    assert settings.session_idle_timeout_seconds == 30 * 60
    assert settings.session_absolute_timeout_seconds == 8 * 60 * 60
    assert settings.session_cookie_secure is False
    assert settings.session_cookie_samesite == "lax"
    assert settings.session_cookie_path == "/"
    assert settings.csrf_header_name == "X-CSRF-Token"
    assert ZoneInfo(settings.workbench_timezone).key == "Asia/Shanghai"
    assert settings.daily_task_summary_hour == 20
    assert settings.feishu_webhook_url is None
    assert settings.feishu_signing_secret is None


def test_workbench_secrets_use_secret_types() -> None:
    settings = Settings(
        _env_file=None,
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/example",
        feishu_signing_secret="local-test-secret",
    )

    assert isinstance(settings.feishu_webhook_url, SecretStr)
    assert isinstance(settings.feishu_signing_secret, SecretStr)
    assert "local-test-secret" not in repr(settings)
    assert all(not name.startswith("vite_") for name in type(settings).model_fields)


def test_production_requires_secure_cookie() -> None:
    with pytest.raises(ValidationError, match="Secure Cookie"):
        Settings(_env_file=None, app_environment="production", session_cookie_secure=False)


def test_summary_timezone_is_fixed_to_asia_shanghai() -> None:
    with pytest.raises(ValidationError, match="WORKBENCH_TIMEZONE"):
        Settings(_env_file=None, workbench_timezone="UTC")


def test_feishu_webhook_must_use_https() -> None:
    with pytest.raises(ValidationError, match="FEISHU_WEBHOOK_URL"):
        Settings(_env_file=None, feishu_webhook_url="http://example.test/hook")
