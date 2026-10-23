"""Capture the screenshots and the screen recording in app_screenshots_screen_recording/.

  docker compose up -d postgres redis && npm --prefix gateway ci && npm --prefix frontend ci
  python -m playwright install chromium
  python scripts/capture_media.py [--base-url https://<demo-host>]   # default: boots a local stack + vite

Every screen shows real responses from the control plane and gateway: the script drives the UI like a user would
(guest login, playground inputs, approve, kill switch, ledger verify). Nothing is mocked in the browser.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app_screenshots_screen_recording"
sys.path.insert(0, str(ROOT / "e2e"))
NPX = shutil.which("npx") or "npx"


@contextlib.contextmanager
def local_app():
    from stack import local_stack, wait

    env = {"DEMO_MODE": "1", "DEMO_EXPIRES_AT": (datetime.now(UTC) + timedelta(days=5)).isoformat(),
           "SESSION_COOKIE_SECURE": "false", "AUDIT_DRAIN_ON_READ": "1", "ALLOWED_ORIGINS": "http://localhost:5199"}
    with local_stack(name="media", db="veyrqun_media", redis_db=8, cp_port=8017, gw_port=8097, llm_port=9251,
                     mcp_port=9252, service_token="media-token", extra_env=env):  # noqa: S106 (throwaway local stack)
        vite = subprocess.Popen([NPX, "vite", "--port", "5199", "--strictPort"], cwd=ROOT / "frontend",
                                env={**os.environ, "VQ_CONTROL_PLANE": "http://127.0.0.1:8017",
                                     "VQ_GATEWAY": "http://127.0.0.1:8097"},
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=os.name == "nt")
        try:
            wait("http://localhost:5199/")
            yield "http://localhost:5199"
        finally:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(vite.pid)], capture_output=True)
            else:
                vite.terminate()


def capture(base: str) -> None:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(exist_ok=True)
    video_dir = OUT / "_video"
    shutil.rmtree(video_dir, ignore_errors=True)
    n = 0

    def shot(page, name: str, full: bool = False) -> None:
        nonlocal n
        n += 1
        page.wait_for_timeout(700)
        page.screenshot(path=str(OUT / f"{n:02d}_{name}.png"), full_page=full)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, record_video_dir=str(video_dir),
                                  record_video_size={"width": 1440, "height": 900}, color_scheme="light")
        page = ctx.new_page()
        page.goto(f"{base}/login")
        page.get_by_role("button", name="Try the live demo").wait_for()
        shot(page, "login_live_demo")
        page.get_by_role("button", name="Try the live demo").click()
        page.get_by_role("heading", name="Playground").wait_for()

        def preset(label: str, name: str) -> None:
            page.get_by_role("button", name=label, exact=True).click()
            # Wait for this request's answer (a real model can take seconds), not for text already on the page.
            with page.expect_response(lambda r: "/playground/run" in r.url, timeout=90_000):
                page.get_by_role("button", name="Send through the gateway").click()
            page.wait_for_timeout(600)
            shot(page, name)

        preset("Within policy", "playground_allowed")
        preset("Email gets masked", "playground_redacted")
        preset("Forbidden tool", "playground_forbidden_tool")
        preset("Prompt injection", "playground_prompt_injection")
        preset("Model leaks a card", "playground_output_leak_blocked")
        preset("Needs approval", "playground_needs_approval")
        with page.expect_response(lambda r: "/playground/run" in r.url, timeout=90_000):
            page.get_by_role("button", name="Approve & re-run").click()
        page.wait_for_timeout(600)
        shot(page, "playground_approved_and_rerun")
        preset("Over the limit", "playground_over_limit")

        page.get_by_role("link", name="Today").click()
        page.get_by_role("heading", name="Today").wait_for()
        shot(page, "today_overview")
        page.get_by_role("link", name="Agents").first.click()
        page.get_by_role("heading", name="Agents").wait_for()
        shot(page, "agents")
        page.get_by_role("link", name="Fee reversal assistant").first.click()
        page.wait_for_selector("text=Policy history")
        shot(page, "agent_detail_policy", full=True)
        page.get_by_role("link", name="Approvals").click()
        page.get_by_role("heading", name="Approvals").wait_for()
        page.get_by_role("button", name="Used").click()
        shot(page, "approvals_history")
        page.get_by_role("link", name="Audit ledger").click()
        page.get_by_role("heading", name="Audit ledger").wait_for()
        page.get_by_role("button", name="Verify whole ledger").click()
        page.wait_for_selector("text=Intact")
        shot(page, "audit_ledger_verified")
        page.locator("tbody tr").first.click()
        page.wait_for_selector("text=hash and signature verified")
        shot(page, "audit_entry_signed")
        page.keyboard.press("Escape")
        page.keyboard.press("Control+k")
        page.keyboard.type("stop")
        shot(page, "command_palette")
        page.keyboard.press("Escape")
        page.get_by_role("link", name="System").click()
        page.get_by_role("button", name="Stop all agents").click()
        page.get_by_label("Type STOP to confirm").fill("STOP")
        page.get_by_role("button", name="Stop the fleet").click()
        page.wait_for_selector("text=Fleet kill switch is ON")
        shot(page, "system_kill_switch_on")
        page.get_by_role("link", name="Playground").click()
        preset("Within policy", "playground_refused_while_stopped")
        page.get_by_role("link", name="System").click()
        page.get_by_role("button", name="Release").click()
        page.locator("dialog[open]").get_by_role("button", name="Release").click()
        page.wait_for_timeout(1200)
        page.evaluate("document.documentElement.dataset.theme = 'dark'")
        page.get_by_role("link", name="Today").click()
        shot(page, "today_dark_mode")
        ctx.close()
        browser.close()

    videos = sorted(video_dir.glob("*.webm"))
    if videos:
        target = OUT / "veyrqun_walkthrough.webm"
        shutil.move(str(videos[-1]), target)
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(target), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-movflags", "+faststart", str(OUT / "veyrqun_walkthrough.mp4")], check=True)
            target.unlink()
    shutil.rmtree(video_dir, ignore_errors=True)
    print(f"wrote {n} screenshots and a walkthrough video to {OUT}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", help="capture a running deployment instead of a local stack")
    a = ap.parse_args()
    if a.base_url:
        capture(a.base_url.rstrip("/"))
    else:
        with local_app() as base:
            time.sleep(1)
            capture(base)


if __name__ == "__main__":
    main()
