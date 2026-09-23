import pytest
from fastapi.testclient import TestClient
from app.config import settings
from app.main import app

client = TestClient(app)

# Skip any test that drives the full RAG pipeline when no real Google key is configured
needs_google_key = pytest.mark.skipif(
    not settings.GOOGLE_API_KEY or "your-google" in settings.GOOGLE_API_KEY,
    reason="GOOGLE_API_KEY not configured — skipping embedding-dependent test"
)


def test_health_check():
    response = client.get("/health")
    assert response.status_code in (200, 503)
    data = response.json()
    assert "status" in data
    assert "app_name" in data
    assert "services" in data
    assert "database" in data["services"]
    assert "vector_db" in data["services"]
    assert "redis" in data["services"]


def test_unauthenticated_request_rejected():
    response = client.post(
        "/api/v1/chat/query",
        json={"query": "Unauthenticated test query"}
    )
    assert response.status_code == 401
    assert "detail" in response.json()


def test_auth_token_generation():
    response = client.post(
        "/api/v1/auth/token",
        json={"user_id": "usr_test", "username": "tester", "password": "password"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@needs_google_key
def test_chat_query_endpoint():
    token_resp = client.post("/api/v1/auth/token", json={"user_id": "usr_test", "username": "tester", "password": "password"})
    token = token_resp.json()["access_token"]

    response = client.post(
        "/api/v1/chat/query",
        json={"query": "Explain the production RAG architecture"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "answer" in data
    assert "citations" in data
    assert "execution_time_ms" in data
    assert "groundedness_score" in data
    assert "cache_hit" in data


@needs_google_key
def test_chat_query_stream_endpoint():
    token_resp = client.post("/api/v1/auth/token", json={"user_id": "usr_test", "username": "tester", "password": "password"})
    token = token_resp.json()["access_token"]

    response = client.post(
        "/api/v1/chat/query/stream",
        json={"query": "Explain async ingestion"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")


def test_document_upload_and_deduplication():
    import uuid
    token_resp = client.post("/api/v1/auth/token", json={"user_id": "usr_test", "username": "tester", "password": "password"})
    token = token_resp.json()["access_token"]

    unique_id = uuid.uuid4().hex[:8]
    file_content = f"Sample document text for async ingestion test in production RAG pipeline {unique_id}.".encode()

    # 1. First upload
    response1 = client.post(
        "/api/v1/documents/upload",
        files={"file": (f"test_doc_dedup_{unique_id}.txt", file_content, "text/plain")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response1.status_code == 200
    data1 = response1.json()
    assert "doc_id" in data1
    assert data1["status"] == "PROCESSING"

    # 2. Duplicate upload — same content, same hash
    response2 = client.post(
        "/api/v1/documents/upload",
        files={"file": ("test_doc_dedup.txt", file_content, "text/plain")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["doc_id"] == data1["doc_id"]
    assert data2["status"] == "DUPLICATE_SKIPPED"


def test_document_list_and_delete():
    token_resp = client.post("/api/v1/auth/token", json={"user_id": "usr_test", "username": "tester", "password": "password"})
    token = token_resp.json()["access_token"]

    # Upload document to delete
    file_content = b"Unique document content for list and delete test run."
    upload_resp = client.post(
        "/api/v1/documents/upload",
        files={"file": ("to_delete.txt", file_content, "text/plain")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert upload_resp.status_code == 200
    doc_id = upload_resp.json()["doc_id"]

    # List documents (with pagination)
    list_resp = client.get("/api/v1/documents?limit=100&offset=0", headers={"Authorization": f"Bearer {token}"})
    assert list_resp.status_code == 200
    doc_ids = [d["id"] for d in list_resp.json()]
    assert doc_id in doc_ids

    # Delete document
    del_resp = client.delete(f"/api/v1/documents/{doc_id}", headers={"Authorization": f"Bearer {token}"})
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "DELETED"


def test_upload_unsupported_mime_type():
    """Verify that binary/executable uploads are rejected with 415."""
    token_resp = client.post("/api/v1/auth/token", json={"user_id": "usr_test", "username": "tester", "password": "password"})
    token = token_resp.json()["access_token"]

    response = client.post(
        "/api/v1/documents/upload",
        files={"file": ("malware.exe", b"MZ\x90\x00", "application/x-msdownload")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 415


def test_upload_file_size_limit():
    token_resp = client.post("/api/v1/auth/token", json={"user_id": "usr_test", "username": "tester", "password": "password"})
    token = token_resp.json()["access_token"]

    # Exceed size limit — use allowed MIME type so size check is reached
    oversized_content = b"A" * (settings.MAX_UPLOAD_SIZE_BYTES + 1024)
    response = client.post(
        "/api/v1/documents/upload",
        files={"file": ("large_file.txt", oversized_content, "text/plain")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 413


def test_task_status_endpoint():
    """Verify task status endpoint handles various task ID formats."""
    token_resp = client.post("/api/v1/auth/token", json={"user_id": "usr_test", "username": "tester", "password": "password"})
    token = token_resp.json()["access_token"]

    # sync_ prefix
    response = client.get("/api/v1/documents/status/sync_doc123")
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"

    # duplicate_skipped
    response2 = client.get("/api/v1/documents/status/duplicate_skipped")
    assert response2.status_code == 200
    assert response2.json()["status"] == "COMPLETED"
