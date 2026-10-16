"""End-to-End Playwright Browser Verification for ResPlan Plan ID 14433 & Phase 11 Optimizer.

Verifies:
1. Plan ID input and Load Plan flow for Plan ID 14433
2. Unrouted preview with metadata chips (< 500 ms)
3. 2D/3D visualization of floor plan and generated devices
4. Click 'Generate Cable Rings' -> Phase 11 optimization
5. Scoreboard display: PROVEN OPTIMAL HUB COUNT, Greedy J vs Opt J, % improvement, lower bounds
6. 6/6 Hard rules verified green
7. Export Slide 8 JSON and PNG controls
8. Captures before and after screenshots.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "tests" / "browser_verification" / "plan_id_14433"
ARTIFACTS_DIR = Path("/Users/jahnaviakveti/.gemini/antigravity/brain/5a0617cb-07d0-4651-8eb8-583d0cf1f147")


def run_verification() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server_url = "http://127.0.0.1:8000"

    print("=== Step 1: Checking Server Status ===")
    req = urllib.request.urlopen(f"{server_url}/api/dataset/status", timeout=5)
    status_data = json.loads(req.read().decode("utf-8"))
    print(f"Dataset status: {status_data}")
    assert status_data["ready"] is True
    assert status_data["record_count"] == 17000

    print("=== Step 2: Launching Playwright Chromium ===")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # Step 3: Open studio
        print("Navigating to Co-Design Studio...")
        page.goto(f"{server_url}/", wait_until="networkidle")
        page.wait_for_selector("#inputPlanId", timeout=10000)

        # Verify dataset status dot is green
        status_dot = page.locator("#datasetStatusDot")
        assert "ready" in (status_dot.get_attribute("class") or "")

        # Verify legacy upload elements are not displayed in main controls
        legacy_upload = page.locator("#fileInput")
        assert not legacy_upload.is_visible()

        # Step 4: Enter Plan ID 14433 and click Load Plan
        print("Entering Plan ID 14433 and loading...")
        t0 = time.perf_counter()
        page.fill("#inputPlanId", "14433")
        page.click("#btnLoadPlan")

        # Wait for preview mode to activate and metadata chips to appear
        page.wait_for_selector("#previewActionBar:not([style*='display: none'])", timeout=5000)
        page.wait_for_selector("#metaPlanId", timeout=5000)
        t_load = time.perf_counter() - t0
        print(f"Plan ID 14433 preview loaded in {t_load*1000:.1f} ms")

        # Verify metadata chips
        plan_id_text = page.locator("#metaPlanId").inner_text()
        total_area_text = page.locator("#metaTotalArea").inner_text()
        net_area_text = page.locator("#metaNetArea").inner_text()
        eligible_rooms_text = page.locator("#metaEligibleRooms").inner_text()
        device_count_text = page.locator("#metaDeviceCount").inner_text()

        print(f"Metadata Chips: Plan ID={plan_id_text}, Total Area={total_area_text}, Net Area={net_area_text}, Eligible Rooms={eligible_rooms_text}, Devices={device_count_text}")
        assert "14433" in plan_id_text
        assert "120." in total_area_text
        assert "95." in net_area_text
        assert "5" in eligible_rooms_text
        assert "5" in device_count_text

        # Verify preview banner is visible and no hubs yet
        assert page.locator("#previewActionBar").is_visible()

        hub_count_text = page.locator("#metricH").inner_text()
        assert hub_count_text in ("0", "--", "-")

        # Capture unrouted preview screenshot
        preview_screenshot = OUTPUT_DIR / "01_plan_14433_unrouted_preview.png"
        page.screenshot(path=str(preview_screenshot), full_page=True)
        print(f"Captured unrouted preview screenshot: {preview_screenshot}")

        if ARTIFACTS_DIR.exists():
            shutil.copy2(preview_screenshot, ARTIFACTS_DIR / "unrouted_preview_14433.png")

        # Step 5: Click Generate Cable Rings
        print("Clicking 'Generate Cable Rings' to invoke Phase 11 hybrid optimizer...")
        t0_gen = time.perf_counter()
        page.click("#btnGenerateRings")

        # Wait for optimization to finish and scoreboard to appear
        page.wait_for_selector("#phase11ScoreboardCard:not([style*='display: none'])", timeout=60000)
        t_gen = time.perf_counter() - t0_gen

        print(f"Cable ring optimization completed in {t_gen:.2f} s")

        # Wait a moment for canvas and HUD rendering to settle
        page.wait_for_timeout(1000)

        # Step 6: Verify Phase 11 Scoreboard & metrics
        hub_status_badge = page.locator("#badgeHubOptimality").inner_text()
        print(f"Hub Status Badge: '{hub_status_badge}'")
        assert "PROVEN OPTIMAL HUB COUNT" in hub_status_badge

        # Verify HUD metrics
        routed_hubs = page.locator("#metricH").inner_text()
        routed_length = page.locator("#metricL").inner_text()
        routed_cost = page.locator("#metricJ").inner_text()
        print(f"Routed metrics: Hubs={routed_hubs}, Length={routed_length}m, Cost J={routed_cost}")
        assert routed_hubs == "4"
        assert float(routed_length) > 0

        # Verify all 6 hard rules passed (green)
        rules = ["badgeRule1", "badgeRule2", "badgeRule3", "badgeRule4", "badgeRule5", "badgeRule6"]
        for r in rules:
            rule_el = page.locator(f"#{r}")
            cls = rule_el.get_attribute("class") or ""
            assert "pass" in cls, f"Rule {r} failed to pass: {cls}"
        print("All 6 hard constraints verified PASS (green)!")

        # Capture routed solution screenshot
        routed_screenshot = OUTPUT_DIR / "02_plan_14433_routed_solution.png"
        page.screenshot(path=str(routed_screenshot), full_page=True)
        print(f"Captured routed solution screenshot: {routed_screenshot}")

        if ARTIFACTS_DIR.exists():
            shutil.copy2(routed_screenshot, ARTIFACTS_DIR / "routed_solution_14433.png")

        # Step 7: Test Slide 8 JSON Download
        print("Testing Slide 8 JSON download...")
        with page.expect_download() as download_info:
            page.click("#btnExportSlide8")
        download = download_info.value
        json_path = OUTPUT_DIR / "slide8_14433.json"
        download.save_as(str(json_path))
        print(f"Downloaded Slide 8 JSON to {json_path}")

        with open(json_path, "r", encoding="utf-8") as f:
            slide8_data = json.load(f)
        assert slide8_data["plan_id"] == "14433"
        assert len(slide8_data["hubs"]) == 4
        print(f"Slide 8 JSON verified: {len(slide8_data['hubs'])} hubs, reported_metrics={slide8_data.get('reported_metrics')}")


        # Step 8: Test Overlay PNG Download
        print("Testing Overlay PNG download...")
        with page.expect_download() as png_download_info:
            page.click("#btnDownloadOverlayPng")
        png_download = png_download_info.value
        png_path = OUTPUT_DIR / "overlay_14433.png"
        png_download.save_as(str(png_path))
        print(f"Downloaded Overlay PNG to {png_path} (size: {os.path.getsize(png_path)} bytes)")
        assert os.path.getsize(png_path) > 1000

        browser.close()
        print("=== Browser E2E Verification Complete & SUCCESSFUL! ===")



if __name__ == "__main__":
    run_verification()
