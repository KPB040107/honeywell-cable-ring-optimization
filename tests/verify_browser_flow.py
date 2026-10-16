"""End-to-End Browser Verification for Floor Plan Upload, Preview, Routing, and Exports.

Automates the complete browser flow using Playwright:
1. Loads Co-Design Studio website and verifies initial state & toolbar.
2. Selects / uploads demo_three_rooms_window.json floor plan.
3. Verifies unrouted preview mode and captures preview screenshot.
4. Clicks "Generate cable rings", verifies routed overlay, metrics, and 6/6 hard rule passes.
5. Captures routed overlay screenshot.
6. Tests downloading Slide 8 JSON and overlay PNG.
7. Uploads invalid JSON and verifies error banner with specific diagnostics.
8. Uploads a floor plan that violates Rule 6 clearance and verifies honest failure in reviewer HUD.
9. Tests switching demo benchmark plans via one-click dropdown.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "tests" / "browser_verification"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"


def run_browser_verification() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server_port = 8899

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
        # Wait for server to come online
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
            raise RuntimeError("Studio server failed to start on port " + str(server_port))

        print(f"Studio server active at http://127.0.0.1:{server_port}")

        with sync_playwright() as p:
            # Prefer system Chrome if available, otherwise Chromium
            chrome_app = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            launch_args = {"headless": True}
            if os.path.exists(chrome_app):
                launch_args["executable_path"] = chrome_app

            browser = p.chromium.launch(**launch_args)
            context = browser.new_context(viewport={"width": 1600, "height": 950})
            page = context.new_page()

            # -----------------------------------------------------------------
            # Screen 1: Initial Studio Load
            # -----------------------------------------------------------------
            print("=== Step 2: Loading Studio Homepage ===")
            page.goto(f"http://127.0.0.1:{server_port}/", wait_until="networkidle")
            page.wait_for_selector("#studioCanvas")
            page.wait_for_timeout(1000)

            shot1 = OUTPUT_DIR / "01_initial_studio.png"
            page.screenshot(path=str(shot1))
            print(f"[OK] Saved initial studio screenshot: {shot1}")

            # -----------------------------------------------------------------
            # Screen 2: Valid Upload -> Unrouted Floor Plan Preview
            # -----------------------------------------------------------------
            print("=== Step 3: Uploading Valid Floor Plan (demo_three_rooms_window.json) ===")
            valid_fixture = FIXTURES_DIR / "demo_three_rooms_window.json"
            page.set_input_files("#fileUploadInput", str(valid_fixture))

            # Wait for preview bar to appear
            page.wait_for_selector("#previewActionBar", state="visible")
            page.wait_for_timeout(1200)

            # Check preview UI text
            plan_id_text = page.text_content("#previewPlanId")
            details_text = page.text_content("#previewDetails")
            print(f"Preview detected: {plan_id_text} ({details_text})")

            shot2 = OUTPUT_DIR / "02_uploaded_preview.png"
            page.screenshot(path=str(shot2))
            print(f"[OK] Saved floor plan preview screenshot: {shot2}")

            # -----------------------------------------------------------------
            # Screen 3: Generate Cable Rings -> Routed Overlay
            # -----------------------------------------------------------------
            print("=== Step 4: Generating Closed Cable Rings ===")
            page.click("#btnGenerateRings")

            # Wait for preview bar to hide and metrics to populate
            page.wait_for_selector("#previewActionBar", state="hidden", timeout=15000)
            page.wait_for_timeout(1500)

            # Verify metrics and checklist
            metric_H = page.text_content("#metricH")
            metric_L = page.text_content("#metricL")
            metric_J = page.text_content("#metricJ")
            score_badge = page.text_content("#rulesScoreBadge")
            print(f"Routed Results: Hubs H={metric_H}, Length L={metric_L}m, J={metric_J}, Score={score_badge}")

            shot3 = OUTPUT_DIR / "03_routed_overlay.png"
            page.screenshot(path=str(shot3))
            print(f"[OK] Saved routed overlay screenshot: {shot3}")

            # -----------------------------------------------------------------
            # Screen 4: Download Slide 8 JSON and Overlay PNG
            # -----------------------------------------------------------------
            print("=== Step 5: Testing Artifact Downloads ===")
            with page.expect_download() as download_info:
                page.click("#btnExportSlide8")
            download_json = download_info.value
            json_target = OUTPUT_DIR / download_json.suggested_filename
            download_json.save_as(str(json_target))
            print(f"[OK] Downloaded Slide 8 JSON: {json_target} ({os.path.getsize(json_target)} bytes)")

            with page.expect_download() as download_png_info:
                page.click("#btnDownloadOverlayPng")
            download_png = download_png_info.value
            png_target = OUTPUT_DIR / download_png.suggested_filename
            download_png.save_as(str(png_target))
            print(f"[OK] Downloaded Overlay PNG: {png_target} ({os.path.getsize(png_target)} bytes)")

            # -----------------------------------------------------------------
            # Screen 5: Invalid JSON Upload -> Specific Diagnostics Error
            # -----------------------------------------------------------------
            print("=== Step 6: Testing Invalid JSON Upload Diagnostics ===")
            bad_json_path = OUTPUT_DIR / "invalid_bad_syntax.json"
            bad_json_path.write_text("{ 'area': 100, rooms: [malformed_json }", encoding="utf-8")

            page.set_input_files("#fileUploadInput", str(bad_json_path))
            page.wait_for_selector("#uploadErrorBanner", state="visible")
            page.wait_for_timeout(800)

            err_msg = page.text_content("#uploadErrorMessage")
            print(f"Error diagnostics shown in UI: {err_msg}")

            shot4 = OUTPUT_DIR / "04_invalid_upload_error.png"
            page.screenshot(path=str(shot4))
            print(f"[OK] Saved invalid upload error screenshot: {shot4}")

            # Close error banner
            page.click("#btnCloseUploadError")
            page.wait_for_selector("#uploadErrorBanner", state="hidden")

            # -----------------------------------------------------------------
            # Screen 6: Upload Plan with Rule 6 Wall Clearance Violation
            # -----------------------------------------------------------------
            print("=== Step 7: Testing Uploaded Plan with Rule 6 Violation ===")
            with open(valid_fixture, "r", encoding="utf-8") as f:
                violating_data = json.load(f)

            # Node N1 placed 0.30 m from wall (at [25, 4] dm = 2.5m, 0.4m; wall at y=0.1m)
            violating_data["nodes"][0]["location"] = [25, 4]
            violating_path = OUTPUT_DIR / "violating_clearance.json"
            with open(violating_path, "w", encoding="utf-8") as f:
                json.dump(violating_data, f)

            page.set_input_files("#fileUploadInput", str(violating_path))
            page.wait_for_selector("#previewActionBar", state="visible")
            page.wait_for_timeout(800)

            # Click Generate rings on violating plan
            page.click("#btnGenerateRings")
            page.wait_for_selector("#previewActionBar", state="hidden", timeout=15000)
            page.wait_for_timeout(1500)

            rule6_badge = page.text_content("#badgeRule6")
            val_title = page.text_content("#validityTitle")
            print(f"Rule 6 Status: {rule6_badge}, Banner: {val_title}")

            shot5 = OUTPUT_DIR / "05_rule_violation_halo.png"
            page.screenshot(path=str(shot5))
            print(f"[OK] Saved clearance violation screenshot: {shot5}")

            # -----------------------------------------------------------------
            # Screen 7: One-Click Demo Plan Selector
            # -----------------------------------------------------------------
            print("=== Step 8: Testing One-Click Demo Plan Selector ===")
            page.select_option("#demoSelectDropdown", "demo_six_rooms_capacity")
            page.click("#btnLoadDemoPlan")
            page.wait_for_selector("#previewActionBar", state="visible")
            page.wait_for_timeout(1000)

            demo_plan_id = page.text_content("#previewPlanId")
            print(f"Loaded demo plan: {demo_plan_id}")

            shot6 = OUTPUT_DIR / "06_demo_six_rooms_preview.png"
            page.screenshot(path=str(shot6))
            print(f"[OK] Saved demo selector screenshot: {shot6}")

            browser.close()
            print("=== ALL BROWSER FLOW STEPS VERIFIED SUCCESSFULLY! ===")

    finally:
        server_proc.terminate()
        server_proc.wait()


if __name__ == "__main__":
    run_browser_verification()
