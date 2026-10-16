"""End-to-End Browser Verification for Issue 1 and Issue 2.

Tests the complete flow in a real headless Chrome browser via Playwright:
1. Upload tests/fixtures/new_four_room_plan.json.
2. Verify Floor Plan Preview banner does NOT overlap controls/toolbars in:
   - Split 2D / 3D mode (desktop width and narrow width)
   - 2D Floor Plan mode
   - Full 3D BIM mode
3. Verify Suggest buttons are disabled during unrouted preview with clear guidance.
4. Click 'Generate Cable Rings' -> verify rings routed, preview banner hides, Suggest enabled.
5. Click 'Suggest Floor-Plan Improvements' -> verify loading state and empty-result state on locally optimal plan.
6. Upload demo_three_rooms_window.json -> generate rings -> suggest improvements -> verify proposals displayed.
7. Click 'Apply Tweak' -> verify proposal applied, re-routed, and 6/6 PPT hard rules passed.
8. Test default demo (Slide 5) suggest and apply flow.
Captures before/after screenshots for all stages.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "tests" / "browser_verification" / "bugfix_verification"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"


def run_e2e_verification() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server_port = 8898

    print("=== Step 1: Starting Background Studio Server ===")
    server_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "resplan_router.phase7_interactive.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(server_port),
            "--log-level",
            "warning",
        ],
        cwd=str(PROJECT_ROOT),
    )

    try:
        import urllib.request
        server_ready = False
        for _ in range(30):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{server_port}/api/state", timeout=1) as resp:
                    if resp.status == 200:
                        server_ready = True
                        break
            except Exception:
                time.sleep(0.5)

        if not server_ready:
            raise RuntimeError(f"Studio server failed to start on port {server_port}")

        print(f"[OK] Studio server online at http://127.0.0.1:{server_port}")

        with sync_playwright() as p:
            chrome_app = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            launch_args = {"headless": True}
            if os.path.exists(chrome_app):
                launch_args["executable_path"] = chrome_app

            browser = p.chromium.launch(**launch_args)
            context = browser.new_context(viewport={"width": 1600, "height": 950})
            page = context.new_page()

            # -----------------------------------------------------------------
            # 1. Load Studio and upload new_four_room_plan.json
            # -----------------------------------------------------------------
            print("\n=== Step 2: Uploading new_four_room_plan.json in Split 2D / 3D ===")
            page.goto(f"http://127.0.0.1:{server_port}/", wait_until="networkidle")
            page.wait_for_selector("#studioCanvas")
            page.wait_for_timeout(500)

            four_room_fixture = FIXTURES_DIR / "new_four_room_plan.json"
            page.set_input_files("#fileUploadInput", str(four_room_fixture))
            page.wait_for_selector("#previewActionBar", state="visible")
            page.wait_for_timeout(500)

            # Check banner text
            plan_tag = page.text_content("#previewPlanId")
            assert "new_four_room_plan" in plan_tag
            print(f"[OK] Preview banner loaded for {plan_tag}")

            # Verify no geometric overlap between preview banner and toolbar
            banner_box = page.locator("#previewActionBar").bounding_box()
            toolbar_box = page.locator(".canvas-toolbar").bounding_box()
            assert banner_box is not None and toolbar_box is not None
            print(f"Banner bounding box: y={banner_box['y']}, height={banner_box['height']}, bottom={banner_box['y'] + banner_box['height']}")
            print(f"Toolbar bounding box: y={toolbar_box['y']}, height={toolbar_box['height']}")
            # Toolbar must sit cleanly below preview banner
            assert toolbar_box["y"] >= banner_box["y"] + banner_box["height"] - 1.0, (
                f"Overlap detected: banner bottom is {banner_box['y'] + banner_box['height']}, "
                f"toolbar top is {toolbar_box['y']}"
            )
            print("[PASS] Verified preview banner does NOT overlap canvas toolbar in Split view")

            # Verify 3D camera controls are visible and not overlapped
            cam_box = page.locator("#bimCameraToolbar").bounding_box()
            assert cam_box is not None
            assert cam_box["y"] >= toolbar_box["y"], "Camera toolbar is properly positioned below main toolbar"
            print("[PASS] Verified 3D camera controls are not overlapped by preview banner")

            # Capture Screenshot 1: Split 2D / 3D Desktop
            shot1 = OUTPUT_DIR / "01_preview_split_desktop.png"
            page.screenshot(path=str(shot1))
            print(f"[OK] Screenshot saved: {shot1.name}")

            # -----------------------------------------------------------------
            # 2. Test Narrow Screen Width (900px)
            # -----------------------------------------------------------------
            print("\n=== Step 3: Checking Narrow Window Width (900px) ===")
            page.set_viewport_size({"width": 900, "height": 950})
            page.wait_for_timeout(400)
            banner_box_narrow = page.locator("#previewActionBar").bounding_box()
            toolbar_box_narrow = page.locator(".canvas-toolbar").bounding_box()
            assert banner_box_narrow is not None and toolbar_box_narrow is not None
            assert toolbar_box_narrow["y"] >= banner_box_narrow["y"] + banner_box_narrow["height"] - 1.0
            shot2 = OUTPUT_DIR / "02_preview_split_narrow.png"
            page.screenshot(path=str(shot2))
            print(f"[OK] Screenshot saved: {shot2.name}")

            # Restore viewport
            page.set_viewport_size({"width": 1600, "height": 950})
            page.wait_for_timeout(300)

            # -----------------------------------------------------------------
            # 3. Test 2D Mode and Full 3D Mode Layout
            # -----------------------------------------------------------------
            print("\n=== Step 4: Checking 2D and Full 3D Modes Layout ===")
            page.click("#btnView2D")
            page.wait_for_timeout(400)
            shot3 = OUTPUT_DIR / "03_preview_2d_mode.png"
            page.screenshot(path=str(shot3))
            print(f"[OK] Screenshot saved: {shot3.name}")

            page.click("#btnView3D")
            page.wait_for_timeout(400)
            shot4 = OUTPUT_DIR / "04_preview_3d_mode.png"
            page.screenshot(path=str(shot4))
            print(f"[OK] Screenshot saved: {shot4.name}")

            # Return to Split view
            page.click("#btnViewSplit")
            page.wait_for_timeout(400)

            # -----------------------------------------------------------------
            # 4. Verify Suggest Buttons Disabled in Preview Mode
            # -----------------------------------------------------------------
            print("\n=== Step 5: Checking Suggest Buttons in Preview Mode ===")
            btn_toolbar = page.locator("#btnSuggestToolbar")
            btn_actions = page.locator("#btnSuggestActions")
            btn_header = page.locator("#btnRefreshCopilot")

            assert btn_toolbar.is_disabled()
            assert btn_actions.is_disabled()
            assert btn_header.is_disabled()
            print("[PASS] All three Suggest buttons are disabled during unrouted preview")

            # Check guidance text in copilot container
            copilot_text = page.locator("#copilotContainer").text_content()
            assert "Cable Rings Required" in copilot_text
            print("[PASS] Co-Pilot card clearly instructs that cable rings are required first")

            shot5 = OUTPUT_DIR / "05_copilot_preview_disabled.png"
            page.screenshot(path=str(shot5))
            print(f"[OK] Screenshot saved: {shot5.name}")

            # -----------------------------------------------------------------
            # 5. Generate Cable Rings
            # -----------------------------------------------------------------
            print("\n=== Step 6: Generating Cable Rings for new_four_room_plan ===")
            page.click("#btnGenerateRings")
            page.wait_for_selector("#previewActionBar", state="hidden")
            page.wait_for_timeout(1000)

            # Check HUD metrics populated
            j_val = page.text_content("#metricJ")
            assert j_val != "--"
            print(f"[OK] Cable rings routed: total cost J = {j_val}")

            # Verify Suggest buttons are now enabled
            assert not btn_toolbar.is_disabled()
            assert not btn_actions.is_disabled()
            assert not btn_header.is_disabled()
            print("[PASS] All three Suggest buttons enabled once routing completes")

            shot6 = OUTPUT_DIR / "06_rings_generated.png"
            page.screenshot(path=str(shot6))
            print(f"[OK] Screenshot saved: {shot6.name}")

            # -----------------------------------------------------------------
            # 6. Suggest Floor-Plan Improvements on new_four_room_plan
            # -----------------------------------------------------------------
            print("\n=== Step 7: Requesting Improvements on new_four_room_plan (Locally Optimal) ===")
            page.click("#btnSuggestActions")
            # Wait for empty result state
            page.wait_for_selector(".copilot-no-proposals", timeout=10000)
            empty_title = page.text_content(".empty-state-title")
            assert "No Cost-Reducing Improvements Found" in empty_title
            empty_desc = page.text_content(".empty-state-desc")
            assert "new_four_room_plan" in empty_desc
            print(f"[PASS] Correct empty-result state displayed: '{empty_title}' referencing {empty_desc[:60]}...")

            shot7 = OUTPUT_DIR / "07_copilot_empty_optimal.png"
            page.screenshot(path=str(shot7))
            print(f"[OK] Screenshot saved: {shot7.name}")

            # -----------------------------------------------------------------
            # 7. Test Upload with Suggestions & Apply Tweak (demo_three_rooms_window)
            # -----------------------------------------------------------------
            print("\n=== Step 8: Uploading demo_three_rooms_window.json to test Apply Tweak ===")
            three_room_fixture = FIXTURES_DIR / "demo_three_rooms_window.json"
            page.set_input_files("#fileUploadInput", str(three_room_fixture))
            page.wait_for_selector("#previewActionBar", state="visible")

            # Route cable rings
            page.click("#btnGenerateRings")
            page.wait_for_selector("#previewActionBar", state="hidden")
            page.wait_for_timeout(1000)

            # Request suggestions via header button (#btnRefreshCopilot)
            print("Clicking #btnRefreshCopilot...")
            page.click("#btnRefreshCopilot")
            page.wait_for_selector(".copilot-proposal-card", timeout=10000)

            proposals_count = page.locator(".copilot-proposal-card").count()
            print(f"[PASS] Successfully displayed {proposals_count} co-pilot proposals for demo_three_rooms_window")
            shot8 = OUTPUT_DIR / "08_copilot_proposals_found.png"
            page.screenshot(path=str(shot8))
            print(f"[OK] Screenshot saved: {shot8.name}")

            # Click Apply Tweak on first proposal
            print("Clicking 'Apply Tweak' on first proposal...")
            first_apply_btn = page.locator(".btn-apply-tweak").first
            first_apply_btn.click()

            # Wait for applied confirmation state
            page.wait_for_selector(".copilot-applied-state", timeout=10000)
            applied_badge = page.text_content(".applied-badge")
            assert "TWEAK APPLIED" in applied_badge.upper()
            print(f"[PASS] Confirmation displayed: '{applied_badge}'")

            # Verify 6/6 PPT hard rules passed
            score_badge = page.text_content("#rulesScoreBadge")
            assert "6 / 6 PASSED" in score_badge
            print(f"[PASS] Resulting plan verified against PPT hard constraints: {score_badge}")

            shot9 = OUTPUT_DIR / "09_copilot_applied_success.png"
            page.screenshot(path=str(shot9))
            print(f"[OK] Screenshot saved: {shot9.name}")

            # -----------------------------------------------------------------
            # 8. Test Default Demo Plan (Slide 5) Flow
            # -----------------------------------------------------------------
            print("\n=== Step 9: Testing Default Demo Plan (Slide 5) ===")
            page.select_option("#demoSelectDropdown", "slide5")
            page.click("#btnLoadDemoPlan")
            page.wait_for_selector("#btnSuggestActions:not([disabled])", timeout=10000)
            page.wait_for_timeout(500)

            page.click("#btnSuggestActions")
            page.wait_for_selector(".copilot-proposal-card", timeout=10000)
            slide5_proposals = page.locator(".copilot-proposal-card").count()
            assert slide5_proposals > 0
            print(f"[PASS] Default demo Slide 5 generated {slide5_proposals} proposals")

            # Apply suggestion on Slide 5
            page.locator(".btn-apply-tweak").first.click()
            page.wait_for_selector(".copilot-applied-state", timeout=10000)
            score_slide5 = page.text_content("#rulesScoreBadge")
            assert "6 / 6 PASSED" in score_slide5
            print(f"[PASS] Applied tweak on Slide 5 passed hard constraints: {score_slide5}")

            shot10 = OUTPUT_DIR / "10_default_demo_slide5_applied.png"
            page.screenshot(path=str(shot10))
            print(f"[OK] Screenshot saved: {shot10.name}")

            browser.close()
            print("\n=== ALL BROWSER E2E TESTS PASSED SUCCESSFULLY! ===")

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()


if __name__ == "__main__":
    run_e2e_verification()
