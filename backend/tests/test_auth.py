"""
Test: Authentication API
========================
5 条功能测试: 注册 / 登录 / me / 登出 / 权限
"""
import pytest


@pytest.mark.asyncio
async def test_register_new_user(client):
    """TC-AUTH-01: 注册新用户"""
    resp = await client.post("/api/auth/register", json={
        "username": "newuser",
        "email": "new@example.com",
        "password": "newpass123",
    })
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    # 返回 TokenResponse (access_token + user_id)
    assert "access_token" in body
    assert "user_id" in body
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_success(client, test_user):
    """TC-AUTH-02: 登录成功"""
    resp = await client.post(
        "/api/auth/login",
        data={"username": "testuser", "password": "testpass123"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "access_token" in body
    assert body.get("token_type") == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client, test_user):
    """TC-AUTH-03: 错误密码登录失败"""
    resp = await client.post(
        "/api/auth/login",
        data={"username": "testuser", "password": "wrong"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_token(client, auth_headers):
    """TC-AUTH-04: 携带 token 访问 /me"""
    resp = await client.get("/api/auth/me", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["username"] == "testuser"


@pytest.mark.asyncio
async def test_me_without_token_denied(client):
    """TC-AUTH-05: 无 token 访问 /me 被拒"""
    resp = await client.get("/api/auth/me")
    assert resp.status_code in (401, 403)
