"""Minimal headless-Chromium driver over the DevTools Protocol.

Why this exists: the booking sites that matter here (ReserveAmerica/Aspira,
Campspot) render availability client-side and sit behind bot protection
(AWS WAF, DataDome) that returns a challenge page to plain HTTP clients.
A real browser executes the challenge and the app, so we drive one.

Pure stdlib + `websockets`. No puppeteer/playwright install needed.
"""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

import websockets

CHROME_CANDIDATES = [
    "chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
]


def find_chrome():
    for name in CHROME_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError("no chromium/chrome binary found on PATH")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Browser:
    """Launches headless Chromium and talks CDP to it."""

    def __init__(self, headless=True, profile_dir=None, timeout=90):
        self.binary = find_chrome()
        self.port = _free_port()
        self.timeout = timeout
        self._tmp = None
        if profile_dir is None:
            self._tmp = tempfile.mkdtemp(prefix="campfinder-chrome-")
            profile_dir = self._tmp
        self.profile_dir = profile_dir
        args = [
            self.binary,
            "--headless=new" if headless else "",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--window-size=1440,2400",
            f"--user-data-dir={profile_dir}",
            f"--remote-debugging-port={self.port}",
            "about:blank",
        ]
        self.proc = subprocess.Popen(
            [a for a in args if a],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.ws_url = self._wait_for_devtools()

    def _wait_for_devtools(self):
        deadline = time.time() + 45
        url = f"http://127.0.0.1:{self.port}/json/version"
        last = None
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("chromium exited during startup")
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    return json.load(r)["webSocketDebuggerUrl"]
            except Exception as exc:  # devtools not up yet
                last = exc
                time.sleep(0.4)
        raise RuntimeError(f"devtools never came up: {last}")

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def fetch(self, url, wait_ms=9000, wait_for_text=None, extra_wait_ms=0, script=None):
        """Load `url`, let the SPA settle, return the rendered DOM.

        wait_for_text: keep polling until this substring appears (or wait_ms
        elapses) - use it to wait for the availability grid specifically
        rather than guessing a fixed delay.
        script: optional JS expression evaluated after load; its JSON-able
        result is returned alongside the DOM.
        """
        return asyncio.run(
            self._fetch(url, wait_ms, wait_for_text, extra_wait_ms, script)
        )

    async def _fetch(self, url, wait_ms, wait_for_text, extra_wait_ms, script):
        async with websockets.connect(
            self.ws_url, max_size=256 * 1024 * 1024, open_timeout=30,
            ping_interval=20, ping_timeout=60,
        ) as ws:
            counter = {"n": 0}
            pending = {}
            loop = asyncio.get_running_loop()

            async def pump():
                # Dedicated reader: CDP interleaves events with responses, so a
                # naive "read until my id shows up" loop deadlocks whenever an
                # event arrives while nothing is awaiting. Route by id instead.
                try:
                    async for raw in ws:
                        data = json.loads(raw)
                        fut = pending.pop(data.get("id"), None)
                        if fut and not fut.done():
                            fut.set_result(data)
                except Exception:
                    pass

            reader = asyncio.create_task(pump())

            async def send(method, params=None, session=None, timeout=None):
                counter["n"] += 1
                mid = counter["n"]
                msg = {"id": mid, "method": method, "params": params or {}}
                if session:
                    msg["sessionId"] = session
                fut = loop.create_future()
                pending[mid] = fut
                await ws.send(json.dumps(msg))
                try:
                    data = await asyncio.wait_for(
                        fut, timeout=timeout or self.timeout
                    )
                except asyncio.TimeoutError:
                    pending.pop(mid, None)
                    return {}
                if "error" in data:
                    raise RuntimeError(data["error"])
                return data.get("result", {})

            async def dom_now(sid):
                res = await send(
                    "Runtime.evaluate",
                    {
                        "expression": "document.documentElement.outerHTML",
                        "returnByValue": True,
                    },
                    sid,
                    timeout=30,
                )
                return (res.get("result") or {}).get("value") or ""

            try:
                target = await send("Target.createTarget", {"url": "about:blank"})
                tid = target["targetId"]
                attached = await send(
                    "Target.attachToTarget", {"targetId": tid, "flatten": True}
                )
                sid = attached["sessionId"]

                await send("Page.enable", {}, sid, timeout=20)
                await send("Runtime.enable", {}, sid, timeout=20)
                # Fire-and-forget: a slow/redirecting nav must not block polling.
                asyncio.create_task(send("Page.navigate", {"url": url}, sid,
                                         timeout=wait_ms / 1000.0 + 30))

                deadline = time.time() + wait_ms / 1000.0
                dom = ""
                settled_at = None
                while time.time() < deadline:
                    await asyncio.sleep(1.0)
                    try:
                        dom = await dom_now(sid)
                    except Exception:
                        continue
                    if wait_for_text:
                        if wait_for_text in dom:
                            break
                    elif len(dom) > 20000:
                        if settled_at is None:
                            settled_at = time.time()
                        elif time.time() - settled_at > 3:
                            break

                if extra_wait_ms:
                    await asyncio.sleep(extra_wait_ms / 1000.0)
                    try:
                        dom = await dom_now(sid) or dom
                    except Exception:
                        pass

                script_result = None
                if script:
                    try:
                        res = await send(
                            "Runtime.evaluate",
                            {"expression": script, "returnByValue": True,
                             "awaitPromise": True},
                            sid,
                            timeout=60,
                        )
                        script_result = (res.get("result") or {}).get("value")
                    except Exception as exc:
                        script_result = {"error": str(exc)}

                try:
                    await send("Target.closeTarget", {"targetId": tid}, timeout=15)
                except Exception:
                    pass
                return {"url": url, "dom": dom, "script": script_result}
            finally:
                reader.cancel()
