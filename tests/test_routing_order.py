
import pytest
from fastapi import FastAPI, APIRouter
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from pathlib import Path

def test_static_shadowing_api():
    """
    Verifies if mounting StaticFiles at "/" before including router blocks API routes.
    """
    app = FastAPI()
    router = APIRouter()

    @router.get("/api/status")
    def status():
        return {"status": "ok"}

    # Create dummy static dir
    static_dir = Path("dummy_static")
    static_dir.mkdir(exist_ok=True)
    (static_dir / "index.html").write_text("<h1>Hello</h1>")

    # 1. FIXED ORDER
    # Include first
    app.include_router(router)
    # Mount second
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    client = TestClient(app)

    # This should fail if the mount catches everything
    response = client.get("/api/status")
    
    # Cleanup
    import shutil
    shutil.rmtree(static_dir)

    # If my hypothesis is correct, this will be 404 because StaticFiles (html=True) handles it 
    # or tries to find a file named 'api/status' and returns 404 from itself.
    assert response.status_code == 200, f"Got {response.status_code}: {response.text}"
