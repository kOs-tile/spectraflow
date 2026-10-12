"""Browser QA for the KAVI Agent Autopsy preview.

Usage from repo checkout:
  pip install playwright
  python -m playwright install chromium
  python portfolio/v3/qa_browser.py

Opens local HTML through an isolated HTTP server; does not call production.
This is a mechanical check. Captured screenshots still require human critique.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent
OUT = Path(os.environ.get("QA_OUTPUT", str(ROOT / "qa-output")))
OUT.mkdir(parents=True, exist_ok=True)


def get_open_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def inspect(page, label, issues):
    dimensions = await page.evaluate(
        """() => ({
          doc:document.documentElement.scrollWidth,
          viewport:window.innerWidth,
          visibleText:!!document.querySelector("h1")?.getBoundingClientRect().width
        })"""
    )
    if dimensions["doc"] > dimensions["viewport"] + 3:
        issues.append(f"{label}: horizontal overflow {dimensions}")
    assert dimensions["visibleText"], f"{label}: missing hero text"


async def run():
    issues = []
    pages = 0
    port = get_open_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
         "--directory", str(ROOT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        time.sleep(0.5)
        async with async_playwright() as playwright:
            chromium = shutil.which("chromium")
            launch_args = {"headless": True}
            if chromium and os.environ.get("USE_SYSTEM_CHROMIUM") == "1":
                launch_args["executable_path"] = chromium
            browser = await playwright.chromium.launch(**launch_args)
            try:
                viewports = [
                    ("desktop", 1440, 900, False),
                    ("mobile", 390, 844, True),
                    ("small-mobile", 360, 740, True)
                ]
                for label, width, height, mobile in viewports:
                    context = await browser.new_context(
                        viewport={"width": width, "height": height},
                        device_scale_factor=1, is_mobile=mobile,
                        has_touch=mobile
                    )
                    page = await context.new_page()
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    response = await page.goto(
                        f"http://127.0.0.1:{port}/index.html",
                        wait_until="domcontentloaded"
                    )
                    assert response and response.status == 200
                    await page.screenshot(path=str(OUT / (label + "-hero.png")))
                    await inspect(page, label + "/hero", issues)
                    # The evidence needs to actually peel during scroll.
                    if label != "small-mobile":
                        await page.evaluate("""() => window.scrollTo(
                            0, Math.round(document.querySelector(".autopsy-scroll").offsetHeight * .42)
                        )""")
                        await page.wait_for_timeout(250)
                        state = await page.locator("#stage-counter").inner_text()
                        assert state != "01 / 04", f"{label}: autopsy timeline did not advance"
                        transform = await page.locator(".sheet-claim").evaluate(
                            "(el) => getComputedStyle(el).transform"
                        )
                        assert transform != "none", f"{label}: specimen never peeled"
                        await page.screenshot(path=str(OUT / (label + "-mid-peel.png")))
                        await page.evaluate("window.scrollTo(0,0)")
                    for anchor in ("authority", "reliability", "memory", "research"):
                        await page.locator("#" + anchor).scroll_into_view_if_needed()
                        await page.screenshot(
                            path=str(OUT / (label + "-" + anchor + ".png"))
                        )
                        await inspect(page, label + "/" + anchor, issues)
                    await page.locator('[data-action="delete"]').click()
                    assert await page.locator("#action-verdict").inner_text() == "BLOCKED"
                    assert await page.locator("#callback-count").inner_text() == "0"
                    await page.locator('[data-action="read"]').click()
                    assert await page.locator("#action-verdict").inner_text() == "ALLOWED"
                    await page.locator("#replay").click()
                    assert "REPLAYING" in (await page.locator("#replay-result").inner_text())
                    await page.locator('#tab-state').focus()
                    await page.keyboard.press("ArrowRight")
                    assert await page.locator('#tab-episode').get_attribute("aria-selected") == "true"
                    assert "Experience needs a timestamp" in await page.locator("#memory-name").inner_text()
                    if errors:
                        issues.append(f"{label}: runtime JS exceptions: " + repr(errors))
                    pages += 1
                    await context.close()
                reduced = await browser.new_context(
                    viewport={"width": 390, "height": 844},
                    reduced_motion="reduce"
                )
                page = await reduced.new_page()
                await page.goto(f"http://127.0.0.1:{port}/index.html")
                assert await page.locator(".sheet-claim").is_visible()
                assert not await page.locator(".sheet-authority").is_visible()
                await reduced.close()
            finally:
                await browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=4)
        except subprocess.TimeoutExpired:
            server.kill()
    result = {
        "status": "PASS" if not issues else "FAIL",
        "viewports_tested": pages,
        "screenshots": len(list(OUT.glob("*.png"))),
        "issues": issues,
        "output": str(OUT),
        "release_note": "Visual design approval is separate from browser QA."
    }
    (OUT / "QA_RESULT.json").write_text(json.dumps(result, indent=2))
    print("KAVI_AGENT_AUTOPSY_QA=" + json.dumps(result))
    if issues:
        raise AssertionError("Browser QA found " + str(len(issues)) + " issues")


if __name__ == "__main__":
    asyncio.run(run())
