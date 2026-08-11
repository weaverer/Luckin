import asyncio
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pwdlib import PasswordHash

from lucking.api.main import create_app
from lucking.config import Settings
from lucking.logging import JsonlLogStore
from tests.integration.test_workbench_api import api_request


def test_passwords_are_argon2_hashes_and_not_recoverable_plaintext() -> None:
    password = "correct horse battery staple"
    encoded = PasswordHash.recommended().hash(password)

    assert encoded.startswith("$argon2")
    assert password not in encoded
    assert PasswordHash.recommended().verify(password, encoded)


def test_cookie_attributes_and_csrf_are_enforced() -> None:
    login = asyncio.run(
        api_request(
            "POST",
            "/api/v1/auth/login",
            json={"username": "analyst", "password": "correct-password"},
        )
    )
    cookie = login.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie

    rejected = asyncio.run(
        api_request(
            "POST",
            "/api/v1/auth/logout",
            cookies={"lucking_session": "session-token"},
        )
    )
    assert rejected.status_code == 403
    assert rejected.json()["code"] == 100003


def test_health_check_uses_request_correlation_without_expanding_public_contract(
    tmp_path: Path,
) -> None:
    app = create_app(Settings(trading_calendar_log_dir=tmp_path))

    async def call_health():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/healthz")

    response = asyncio.run(call_health())
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
    assert "/healthz" not in app.openapi()["paths"]


def test_security_secrets_and_diagnostics_never_reach_api_or_logs(tmp_path: Path) -> None:
    webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/security-test-secret"
    signing_secret = "signing-secret-security-test"
    provider_token = "provider-token-security-test"
    database_password = "database-password-security-test"
    raw_response = '{"private_account":"should-never-leak"}'
    settings = Settings(
        trading_calendar_log_dir=tmp_path,
        feishu_webhook_url=webhook,
        feishu_signing_secret=signing_secret,
    )
    app: FastAPI = create_app(settings)

    @app.get("/_security-probe")
    async def security_probe() -> None:
        raise RuntimeError(
            f"Traceback (most recent call last): token={provider_token} "
            f"password={database_password} provider raw response={raw_response} "
            "SELECT * FROM app_user"
        )

    async def call_probe() -> tuple[int, str]:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/_security-probe")
            return response.status_code, response.text

    status, body = asyncio.run(call_probe())
    assert status == 500
    assert "服务暂时不可用" in body

    store = JsonlLogStore(tmp_path, filename="security.jsonl")
    store.write(
        "security_probe",
        error_summary=(
            f"webhook={webhook} signing_secret={signing_secret} token={provider_token} "
            f"password={database_password} provider raw response={raw_response} "
            "SELECT * FROM app_user\nTraceback (most recent call last): stack"
        ),
    )
    combined = (
        body
        + "\n"
        + "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.glob("*.jsonl"))
    )
    for forbidden in (
        webhook,
        signing_secret,
        provider_token,
        database_password,
        raw_response,
        "SELECT * FROM app_user",
        "Traceback (most recent call last)",
    ):
        assert forbidden not in combined


def test_frontend_source_and_build_contain_no_server_secret_values() -> None:
    roots = [Path("frontend/src"), Path("frontend/dist")]
    forbidden = (
        "security-test-secret",
        "signing-secret-security-test",
        "database-password-security-test",
        "provider-token-security-test",
    )
    content = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for root in roots
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and path.suffix not in {".png", ".woff", ".woff2"}
    )
    assert all(secret not in content for secret in forbidden)
