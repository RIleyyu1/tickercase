"""Measured behavior (Lab Manual 10.4): run the same claim through the real page many times and keep the stage timings.

Usage:
    python scripts/measure_runs.py --runs 30 --claim "NVDA will hit $300 in 3 years"

What it does:
    1. starts the Streamlit page with TICKERCASE_TIMING_DIR (the service writes one timeline file for each call, see
       src/tickercase/timing.py) and TICKERCASE_TIMING_COLD=1 (each run starts with no response held in memory);
    2. opens the page in a headless Chrome and plays the user: type the claim, press READ, press CONFIRM, press RUN;
    3. writes the times of the user's side of each boundary (key pressed, page ready) to <out>/runs/run_NN.json.

``scripts/timing_report.py <out>`` then joins the two sides into one table for each run and the statistics.

The script user does not read or think, so its own times are not those of a person. They are kept apart in the report.
Needs Chrome and the ``websockets`` package. Live mode sends real requests to the public sources, about 110 for each
run, under the same spacing rules as any run of the product.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]
W, H = 1600, 900


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class Page:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def call(self, method, **params):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == self.n:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    async def js(self, expression):
        result = await self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        return result.get("result", {}).get("value")

    async def busy(self) -> bool:
        return bool(await self.js("!!document.querySelector('[data-testid=\"stStatusWidget\"]')"))

    async def idle(self, first=0.3, settle=3, step=0.1, limit=120.0) -> None:
        """Wait until the page has stopped running: ``settle`` quiet looks in a row."""
        await asyncio.sleep(first)
        quiet, started = 0, time.time()
        while quiet < settle and time.time() - started < limit:
            quiet = 0 if await self.busy() else quiet + 1
            await asyncio.sleep(step)

    async def wait_for(self, expression, limit=180.0, step=0.05) -> bool:
        started = time.time()
        while time.time() - started < limit:
            if await self.js(expression):
                return True
            await asyncio.sleep(step)
        return False

    async def rect(self, selector, text=None):
        pick = f".filter(e => e.innerText.includes({json.dumps(text)}))" if text else ""
        return await self.js(
            f"(() => {{ const els = [...document.querySelectorAll({json.dumps(selector)})]{pick}; if (!els.length) return null;"
            " els[0].scrollIntoView({block: 'center'}); const r = els[0].getBoundingClientRect();"
            " return {x: r.left, y: r.top, w: r.width, h: r.height}; })()")

    async def click(self, selector, text=None) -> None:
        if not await self.rect(selector, text):
            raise RuntimeError(f"nothing to click for {selector} {text or ''}")
        await asyncio.sleep(0.15)
        r = await self.rect(selector, text)
        x, y = r["x"] + r["w"] / 2, r["y"] + r["h"] / 2
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            await self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)


async def one_run(page: Page, url: str, claim: str, mode: str) -> dict:
    """Play one user through the three input steps. Every time is seconds since the epoch (time.time())."""
    marks: dict = {"claim": claim, "mode": mode, "started_at": utc()}
    marks["t_open"] = time.time()
    await page.call("Page.navigate", url=url)
    await page.wait_for("!!document.querySelector('.st-key-f_claim_text textarea')", limit=60)
    await page.idle(first=1.0)
    await page.click(".st-key-lang label", "EN")
    await page.idle()
    if mode != "live":
        await page.click(".st-key-sec_mode label", "SYN" if mode == "synthetic" else mode.upper())
        await page.idle()
    marks["t_page_ready"] = time.time()

    await page.click(".st-key-f_claim_text textarea")
    await page.call("Input.insertText", text=claim)
    marks["t_claim_typed"] = time.time()
    await page.click(".st-key-btn_prefill button")
    marks["t_read_pressed"] = time.time()
    await page.wait_for("!!document.querySelector('.st-key-btn_confirm button')", limit=90)
    await page.idle(first=0.1)
    marks["t_check_ready"] = time.time()

    await page.click(".st-key-btn_confirm button")
    marks["t_confirm_pressed"] = time.time()
    await page.wait_for("(() => { const b = document.querySelector('.st-key-btn_run button'); return b ? !b.disabled : false; })()", limit=60)
    await page.idle(first=0.1)
    marks["t_run_enabled"] = time.time()

    await page.click(".st-key-btn_run button")
    marks["t_run_pressed"] = time.time()
    done = await page.wait_for(
        "(() => { const b = document.querySelector('.st-key-nav_answer button'); return b ? !b.disabled : false; })()", limit=240)
    marks["t_report_first_seen"] = time.time()
    await page.idle(first=0.1)
    marks["t_report_ready"] = time.time()
    marks["completed"] = bool(done)
    marks["answer_text"] = (await page.js("document.querySelector('[data-testid=stMain]').innerText.slice(0, 400)") or "")
    return marks


def wait_http(url: str, limit: float = 90.0) -> None:
    started = time.time()
    while time.time() - started < limit:
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"{url} did not answer within {limit:.0f} s")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--claim", default="NVDA will hit $300 in 3 years")
    parser.add_argument("--mode", default="live", choices=["live", "synthetic"])
    parser.add_argument("--out", default="")
    parser.add_argument("--port", type=int, default=8621)
    parser.add_argument("--pause", type=float, default=4.0, help="seconds between runs")
    parser.add_argument("--warm", action="store_true", help="share one service between the runs (responses stay cached)")
    args = parser.parse_args()

    import websockets  # noqa: PLC0415 - only this script needs it

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else ROOT / "data" / "timings" / f"{stamp}_{args.mode}_{'warm' if args.warm else 'cold'}"
    (out / "runs").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TICKERCASE_TIMING_DIR=str(out / "timelines"), TICKERCASE_CASE_DIR=str(out / "cases"),
               TICKERCASE_BOOT_SCREEN="0", TICKERCASE_TIMING_COLD="0" if args.warm else "1")
    chrome = next((c for c in CHROME_CANDIDATES if Path(c).is_file()), None)
    if chrome is None:
        raise RuntimeError("Chrome was not found")

    log = open(out / "streamlit.log", "w", encoding="utf-8")
    server = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"), "--server.port", str(args.port),
                               "--server.headless", "true", "--browser.gatherUsageStats", "false"],
                              cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    debug_port = args.port + 1000
    profile = tempfile.mkdtemp(prefix="tc-measure-")
    browser = subprocess.Popen([chrome, "--headless=new", f"--remote-debugging-port={debug_port}", f"--user-data-dir={profile}",
                                "--hide-scrollbars", f"--window-size={W},{H}", "about:blank"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://localhost:{args.port}/"
    meta = {"claim": args.claim, "mode": args.mode, "cold": not args.warm, "runs_asked": args.runs, "started_at": utc(), "url": url}
    try:
        wait_http(url + "_stcore/health")
        wait_http(f"http://127.0.0.1:{debug_port}/json")
        tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json").read())
        ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page")
        async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
            page = Page(ws)
            await page.call("Runtime.enable")
            await page.call("Emulation.setFocusEmulationEnabled", enabled=True)
            await page.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)
            for n in range(1, args.runs + 1):
                try:
                    marks = await one_run(page, url, args.claim, args.mode)
                except Exception as exc:  # keep the failed run in the record and go on
                    marks = {"error": f"{type(exc).__name__}: {exc}", "started_at": utc()}
                marks["run"] = n
                (out / "runs" / f"run_{n:02d}.json").write_text(json.dumps(marks, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
                took = marks.get("t_report_ready", 0) - marks.get("t_run_pressed", 0) if "t_report_ready" in marks else None
                print(f"run {n:02d}: {'RUN to report %.1f s' % took if took else marks.get('error', 'not completed')}", flush=True)
                await page.call("Page.navigate", url="about:blank")  # ends the session of this run
                await asyncio.sleep(args.pause)
            await page.call("Browser.close")
    finally:
        meta["finished_at"] = utc()
        (out / "meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
        for proc in (browser, server):
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        log.close()
    print("written to", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
