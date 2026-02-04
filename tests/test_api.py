"""
Integration tests for the Whisper Server API authentication and endpoints.
"""
import os
import pytest
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, SQLModel
from sqlmodel.pool import StaticPool
from unittest.mock import patch, MagicMock

# Add src to path for testing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import the app and models
from ws_server.api.app import app
from core.db.models import Job, JobStatus
from core.db.engine import engine as real_engine


# Test client with dependency override for database
@pytest.fixture(name="session")
def session_fixture():
    """Create a test database session."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    """Create a test client with dependency overrides."""
    def get_session_override():
        return session
    
    # Override the get_db_session dependency
    from ws_server.api.endpoints import get_db_session
    app.dependency_overrides[get_db_session] = get_session_override
    
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def api_key():
    """Get or set a test API key."""
    test_key = "test_api_key_12345"
    with patch.dict(os.environ, {"API_KEY": test_key}):
        yield test_key


class TestAuthentication:
    """Test API key authentication."""
    
    def test_missing_api_key(self, client):
        """Test that requests without API key are rejected."""
        response = client.get("/jobs/")
        assert response.status_code == 401
        assert "Missing API Key" in response.json()["detail"]
    
    def test_invalid_api_key(self, client):
        """Test that requests with invalid API key are rejected."""
        response = client.get(
            "/jobs/",
            headers={"X-API-Key": "invalid_key"}
        )
        assert response.status_code == 401
        assert "Invalid API Key" in response.json()["detail"]
    
    def test_valid_api_key(self, client, api_key):
        """Test that requests with valid API key are accepted."""
        response = client.get(
            "/jobs/",
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 200


class TestJobEndpoints:
    """Test job-related endpoints."""
    
    def test_get_jobs_empty(self, client, session, api_key):
        """Test getting jobs when database is empty."""
        response = client.get(
            "/jobs/",
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 200
        assert response.json() == []
    
    def test_get_jobs_with_data(self, client, session, api_key):
        """Test getting jobs when there are jobs in the database."""
        # Create test jobs
        job1 = Job(
            filename="test1.mp3",
            upload_name="original1.mp3",
            status=JobStatus.PENDING
        )
        job2 = Job(
            filename="test2.mp3",
            upload_name="original2.mp3",
            status=JobStatus.COMPLETED,
            transcript="Test transcript"
        )
        session.add(job1)
        session.add(job2)
        session.commit()
        
        response = client.get(
            "/jobs/",
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 200
        jobs = response.json()
        assert len(jobs) == 2
    
    def test_get_job_by_id(self, client, session, api_key):
        """Test getting a specific job by ID."""
        job = Job(
            filename="test.mp3",
            upload_name="original.mp3",
            status=JobStatus.PENDING
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        
        response = client.get(
            f"/job/{job.id}",
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["filename"] == "test.mp3"
        assert data["status"] == JobStatus.PENDING
    
    def test_get_nonexistent_job(self, client, session, api_key):
        """Test getting a job that doesn't exist."""
        response = client.get(
            "/job/99999",
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 404


class TestFileValidation:
    """Test file upload validation."""
    
    def test_invalid_file_type(self, client, api_key):
        """Test that invalid file types are rejected."""
        # Create a fake text file
        files = {"audio_files": ("test.txt", b"not an audio file", "text/plain")}
        response = client.post(
            "/asr-async",
            files=files,
            data={"keep": True, "translate": False},
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 415
        assert "Unsupported file type" in response.json()["detail"]


class TestJobStatusTracking:
    """Test job status tracking functionality."""
    
    def test_job_created_with_pending_status(self, session):
        """Test that new jobs are created with PENDING status."""
        job = Job(
            filename="test.mp3",
            upload_name="original.mp3"
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        
        assert job.status == JobStatus.PENDING
        assert job.created_at is not None
        assert job.updated_at is not None
    
    def test_job_error_message_stored(self, session):
        """Test that error messages are properly stored."""
        job = Job(
            filename="test.mp3",
            upload_name="original.mp3",
            status=JobStatus.FAILED,
            error_message="Test error message"
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        
        assert job.status == JobStatus.FAILED
        assert job.error_message == "Test error message"
