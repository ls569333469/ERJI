from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from engine.config import Settings, settings
from engine.pd2 import auth
from services.api.main import app


def test_subpath_login_csrf_logout_cookie_scope(monkeypatch, tmp_path):
    token = "deployment-test-token-not-a-secret-123456789"
    secret_file = tmp_path / "admin-token.txt"
    secret_file.write_text(token)
    cfg = settings()
    monkeypatch.setattr(cfg, "require_auth", True)
    monkeypatch.setattr(cfg, "admin_token_file", str(secret_file))
    monkeypatch.setattr(cfg, "cookie_path", "/erji")
    monkeypatch.setattr(cfg, "web_origin", "https://xiexiu.io")
    monkeypatch.setattr(auth, "login_allowed", lambda client: True)
    # No startup context: this test intentionally does not use the database.
    client = TestClient(app)
    for path in ("/v1/markets", "/v1/stream"):
        assert client.get(path).status_code == 401
    headers = {"x-erji-client": "local-ui", "origin": cfg.web_origin}
    response = client.post("/v1/auth/login", json={"token": token}, headers=headers)
    assert response.status_code == 200
    for cookie in response.headers.get_list("set-cookie"):
        assert "Path=/erji" in cookie and "Secure" in cookie
        assert "Domain=" not in cookie
    session = response.cookies["erji_session"]
    csrf = response.cookies["erji_csrf"]
    # Nginx/Web keep cookies while forwarding the unprefixed internal API path.
    headers["cookie"] = "erji_session=" + session + "; erji_csrf=" + csrf
    assert client.post("/v1/auth/logout", headers=headers).status_code == 403
    headers["x-erji-csrf"] = csrf
    assert client.post("/v1/auth/logout", headers={**headers, "origin": cfg.web_origin + "/erji"}).status_code == 403
    logout = client.post("/v1/auth/logout", headers=headers)
    assert logout.status_code == 200
    for cookie in logout.headers.get_list("set-cookie"):
        assert "Path=/erji" in cookie and "Max-Age=0" in cookie


def test_cookie_path_validation():
    assert Settings(_env_file=None, cookie_path="/erji").cookie_path == "/erji"
    for value in ("erji", "/erji/", "/erji; Domain=example.com", "/erji\r\n"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, cookie_path=value)
