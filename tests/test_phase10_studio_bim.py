"""Phase 10: Integration tests for Studio 3D BIM Split-Screen, WebGL Viewer & Live HUD.

Validates:
1. REST API endpoint GET /api/bim/scene returning valid BIM3DScene payload.
2. REST API endpoint GET /api/bim/export/obj downloading valid Wavefront OBJ model.
3. Offline vendor assets /vendor/three.min.js and /vendor/OrbitControls.js served with 200 OK.
4. Studio frontend index.html containing #bimViewport, view mode switcher, and 3D camera presets.
5. IncrementalCoDesignEngine get_bim_scene() and export_obj() integration.
"""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from resplan_router.contracts import BIM3DScene, BIMElementType
from resplan_router.phase7_interactive.incremental_router import IncrementalCoDesignEngine
from resplan_router.phase7_interactive.server import app, engine


@pytest.fixture
def test_client() -> TestClient:
    """Fixture providing FastAPI TestClient."""
    return TestClient(app)


def test_bim_scene_rest_api(test_client: TestClient) -> None:
    """Test 1: Verify GET /api/bim/scene returns 200 OK with valid BIM3DScene payload."""
    response = test_client.get("/api/bim/scene")
    assert response.status_code == 200
    data = response.json()

    # Validate against Pydantic contract
    scene = BIM3DScene.model_validate(data)
    assert len(scene.elements) > 0
    assert scene.ceiling_height_m == 2.70
    assert scene.conduit_elevation_m == 2.55

    # Check presence of all element types
    types = {el.element_type for el in scene.elements}
    assert BIMElementType.SLAB in types
    assert BIMElementType.WALL in types
    assert BIMElementType.DOOR_LINTEL in types
    assert BIMElementType.NODE in types
    assert BIMElementType.HUB in types
    assert BIMElementType.CONDUIT in types

    # Check Reviewer Scoreboard metrics HUD formula breakdown
    assert "metrics_hud" in data
    assert data["metrics_hud"].get("formula") == "J = L + 5X + O + 0.5B"


def test_bim_obj_export_rest_api(test_client: TestClient) -> None:
    """Test 2: Verify GET /api/bim/export/obj returns 200 OK with valid Wavefront OBJ syntax."""
    response = test_client.get("/api/bim/export/obj")
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")

    obj_content = response.text
    assert "# Wavefront OBJ" in obj_content
    assert "v " in obj_content
    assert "vn " in obj_content
    assert "f " in obj_content
    assert "g slab_" in obj_content
    assert "g wall_" in obj_content
    assert "g conduit_" in obj_content


def test_vendor_assets_served(test_client: TestClient) -> None:
    """Test 3: Verify vendor 3D assets three.min.js and OrbitControls.js are served offline."""
    r_three = test_client.get("/vendor/three.min.js")
    assert r_three.status_code == 200
    assert len(r_three.content) > 50000

    r_orbit = test_client.get("/vendor/OrbitControls.js")
    assert r_orbit.status_code == 200
    assert len(r_orbit.content) > 10000


def test_studio_static_contains_split_screen_elements() -> None:
    """Test 4: Verify index.html contains #bimViewport, view mode switcher, and 3D camera controls."""
    index_path = Path(__file__).parent.parent / "src" / "resplan_router" / "phase7_interactive" / "static" / "index.html"
    assert index_path.exists(), f"Missing index.html at {index_path}"

    html_content = index_path.read_text(encoding="utf-8")

    # View Mode Switcher
    assert 'id="viewModeSwitcher"' in html_content
    assert 'data-mode="2d"' in html_content
    assert 'data-mode="split"' in html_content
    assert 'data-mode="3d"' in html_content

    # 3D BIM Viewport Container
    assert 'id="viewportContainer"' in html_content
    assert 'id="bimViewport"' in html_content
    assert 'id="studioCanvas"' in html_content

    # 3D Camera Preset Overlay Toolbar
    assert 'id="bimCameraToolbar"' in html_content
    assert 'id="btnCamIsometric"' in html_content
    assert 'id="btnCamTopDown"' in html_content
    assert 'id="btnCamPerspective"' in html_content
    assert 'id="btnCamReset"' in html_content

    # Download 3D BIM OBJ Button
    assert 'id="btnDownloadBimObj"' in html_content

    # Enhanced Formula Scoreboard Breakdown
    assert "J = L + 5X + O + 0.5B" in html_content
    assert 'id="formulaBreakdownBar"' in html_content
    assert 'id="pillValL"' in html_content
    assert 'id="pillValX"' in html_content
    assert 'id="pillValO"' in html_content
    assert 'id="pillValB"' in html_content
    assert 'id="pillValJ"' in html_content

    # Vendor scripts inclusion
    assert 'src="vendor/three.min.js"' in html_content
    assert 'src="vendor/OrbitControls.js"' in html_content


def test_incremental_router_bim_methods() -> None:
    """Test 5: Verify IncrementalCoDesignEngine BIM methods and caching."""
    eng = IncrementalCoDesignEngine()
    scene = eng.get_bim_scene()
    assert isinstance(scene, BIM3DScene)
    assert len(scene.elements) > 0

    # Cache hit check
    assert eng.get_bim_scene() is scene

    # OBJ export
    obj_str = eng.export_obj()
    assert isinstance(obj_str, str)
    assert "v " in obj_str
    assert "f " in obj_str


def test_state_includes_bim_scene_when_requested(test_client: TestClient) -> None:
    """Test 6: Verify GET /api/state?include_bim=true includes full bim_scene payload."""
    r_plain = test_client.get("/api/state")
    assert r_plain.status_code == 200
    assert "bim_scene" not in r_plain.json()

    r_bim = test_client.get("/api/state?include_bim=true")
    assert r_bim.status_code == 200
    data = r_bim.json()
    assert "bim_scene" in data
    assert len(data["bim_scene"]["elements"]) > 0
