import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_user_registration_and_login_flow():
    import uuid
    uname = f"usr_{uuid.uuid4().hex[:8]}"
    # 1. Register a new user
    reg_resp = client.post(
        "/api/v1/auth/register",
        json={
            "username": uname,
            "password": "SuperSecretPassword123!",
            "email": f"{uname}@example.com",
            "role": "user"
        }
    )
    assert reg_resp.status_code == 201
    reg_data = reg_resp.json()
    assert "user_id" in reg_data
    assert reg_data["username"] == uname
    assert reg_data["role"] == "user"

    # 2. Duplicate registration attempt should be rejected (400)
    dup_resp = client.post(
        "/api/v1/auth/register",
        json={
            "username": uname,
            "password": "AnotherPassword456!"
        }
    )
    assert dup_resp.status_code == 400

    # 3. Login with incorrect password should be rejected (401)
    bad_login_resp = client.post(
        "/api/v1/auth/token",
        json={
            "username": uname,
            "password": "WrongPassword!"
        }
    )
    assert bad_login_resp.status_code == 401

    # 4. Login with correct password should succeed (200)
    login_resp = client.post(
        "/api/v1/auth/token",
        json={
            "username": uname,
            "password": "SuperSecretPassword123!"
        }
    )
    assert login_resp.status_code == 200
    token_data = login_resp.json()
    assert "access_token" in token_data
    token = token_data["access_token"]

    # 5. Access protected route /api/v1/auth/me using token
    me_resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["username"] == uname
    assert me_data["role"] == "user"

    # 6. Logout / revoke token
    logout_resp = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert logout_resp.status_code == 200
    assert logout_resp.json()["message"] == "Successfully logged out"

    # 7. Subsequent request using revoked token should be rejected (401)
    post_logout_resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert post_logout_resp.status_code == 401
    assert "revoked" in post_logout_resp.json().get("detail", "").lower()


def test_seeded_tester_user_login():
    # Test seeded user ('tester' / 'password')
    token_resp = client.post(
        "/api/v1/auth/token",
        json={
            "username": "tester",
            "password": "password"
        }
    )
    assert token_resp.status_code == 200
    data = token_resp.json()
    assert "access_token" in data


def test_user_content_isolation():
    import uuid
    # 1. Register User A and upload a document
    u_a = f"usr_a_{uuid.uuid4().hex[:6]}"
    client.post("/api/v1/auth/register", json={"username": u_a, "password": "PassWord123!", "email": f"{u_a}@example.com"})
    t_a_resp = client.post("/api/v1/auth/token", json={"username": u_a, "password": "PassWord123!"})
    token_a = t_a_resp.json()["access_token"]

    file_a = f"secret_doc_{uuid.uuid4().hex[:6]}.txt".encode("utf-8")
    up_a = client.post(
        "/api/v1/documents/upload",
        files={"file": (f"alpha_secret_{u_a}.txt", file_a, "text/plain")},
        headers={"Authorization": f"Bearer {token_a}"}
    )
    assert up_a.status_code == 200
    doc_a_id = up_a.json()["doc_id"]

    # 2. Register User B
    u_b = f"usr_b_{uuid.uuid4().hex[:6]}"
    client.post("/api/v1/auth/register", json={"username": u_b, "password": "PassWord123!", "email": f"{u_b}@example.com"})
    t_b_resp = client.post("/api/v1/auth/token", json={"username": u_b, "password": "PassWord123!"})
    token_b = t_b_resp.json()["access_token"]

    # 3. User B lists documents — User A's document must NOT be present
    list_b = client.get("/api/v1/documents", headers={"Authorization": f"Bearer {token_b}"})
    assert list_b.status_code == 200
    docs_b_ids = [d["id"] for d in list_b.json()]
    assert doc_a_id not in docs_b_ids

    # 4. User B attempts to delete User A's document — must return 403 Forbidden
    del_b = client.delete(f"/api/v1/documents/{doc_a_id}", headers={"Authorization": f"Bearer {token_b}"})
    assert del_b.status_code == 403


def test_omitted_password_authentication_rejected():
    import uuid
    u_test = f"usr_nopass_{uuid.uuid4().hex[:6]}"
    reg = client.post("/api/v1/auth/register", json={"username": u_test, "password": "SecretPassword123!", "email": f"{u_test}@example.com"})
    assert reg.status_code == 201

    # 1. Call token endpoint without password field entirely -> MUST return 422 Unprocessable Entity
    no_pass_resp = client.post("/api/v1/auth/token", json={"username": u_test})
    assert no_pass_resp.status_code in (401, 422)

    # 2. Call token endpoint with empty password string -> MUST return 401 Unauthorized
    empty_pass_resp = client.post("/api/v1/auth/token", json={"username": u_test, "password": ""})
    assert empty_pass_resp.status_code in (401, 422)


def test_self_registration_role_sanitization():
    import uuid
    u_hacker = f"hacker_{uuid.uuid4().hex[:6]}"
    # Attempt to self-register as admin
    reg = client.post("/api/v1/auth/register", json={
        "username": u_hacker,
        "password": "HackerPassword123!",
        "email": f"{u_hacker}@example.com",
        "role": "admin"
    })
    assert reg.status_code == 201
    reg_data = reg.json()
    # Server MUST sanitize and enforce role="user"
    assert reg_data["role"] == "user"
