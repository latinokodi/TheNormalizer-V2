"""The window, rendered and driven by a real browser against the engine that serves it.

This is the check the unit suite cannot be. `backend/tests` never opens a page, so nothing in it proves
that the built interface loads *from the engine's own origin*, that React mounts, that the footer gets its
health answer from the engine, or that moving a setting enables the right controls. All four are visible
only in a browser.

    venv\\Scripts\\python.exe scripts\\check_interface.py

## Why the DevTools protocol and not `--dump-dom`

`--dump-dom` was the first attempt, and it cannot work on this page — for a reason that is a fact about the
product rather than about the flag. **The window holds an open event stream**: `/api/events` is a
long-lived response the engine never closes, so the document never reaches the idle state a one-shot dump
waits for. Under `--headless=new` the dump fired before the page's own `fetch` calls settled, which made
the health lamp read "asking" on a machine whose engine was answering; under `--headless=old` with a
virtual-time budget it never returned at all.

So Chromium is started with a debugging port and driven over its own protocol, which is what a real driver
does. That also buys a **screenshot**, which is the only way to check a layout — the unit suite can prove
the classes are there and not that they add up to a panel.

## What it needs

The engine on 8767, a Chromium-based browser, and `websocket-client` to speak the protocol. Each missing
piece is named and exits non-zero rather than pretending to have checked something.
"""

from __future__ import annotations

import base64
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = "http://127.0.0.1:8767"
SHOTS = ROOT / "docs" / "shots"

#: The browsers to try, in order: Chrome's protocol support is the best-behaved of the two on Windows, and
#: Edge is on every supported machine.
BROWSERS = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)

FAILURES: list[str] = []


def check(claim: str, ok: bool, detail: str) -> None:
    mark = "ok  " if ok else "FAIL"
    print(f"  {mark} {claim}: {detail}")
    if not ok:
        FAILURES.append(f"{claim}: {detail}")


def browser() -> Path | None:
    for candidate in BROWSERS:
        if candidate.is_file():
            return candidate
    for name in ("chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def engine() -> dict | None:
    try:
        with urllib.request.urlopen(f"{BACKEND}/api/health", timeout=10) as answer:
            body = json.loads(answer.read())
            return body if body.get("product") == "TheNormalizer" else None
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Page:
    """One browser tab, driven over the DevTools protocol."""

    def __init__(self, connection, timeout: float = 40.0) -> None:
        self._socket = connection
        self._timeout = timeout
        self._next = 0
        self.console: list[str] = []

    def call(self, method: str, **params):
        self._next += 1
        ident = self._next
        self._socket.send(json.dumps({"id": ident, "method": method, "params": params}))
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            message = json.loads(self._socket.recv())
            kind = message.get("method")
            if kind == "Runtime.consoleAPICalled":
                arguments = message["params"].get("args", [])
                text = " ".join(str(one.get("value", one.get("description", ""))) for one in arguments)
                # Only what the *page* logged as an error is a finding. A browser-level line about the
                # event stream reconnecting is a page that is working.
                if message["params"].get("type") == "error":
                    self.console.append(text)
                continue
            if kind == "Runtime.exceptionThrown":
                self.console.append(f"error: {message['params']['exceptionDetails'].get('text')}")
                continue
            if message.get("id") == ident:
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {message['error']}")
                return message.get("result")
        raise TimeoutError(f"{method} did not answer within {self._timeout:.0f}s")

    def script(self, expression: str):
        """One expression, evaluated as a function body. A `return` is expected inside it."""
        result = self.call(
            "Runtime.evaluate",
            expression=f"(() => {{ {expression} }})()",
            returnByValue=True,
            awaitPromise=True,
        )
        outcome = result.get("result", {})
        if outcome.get("subtype") == "error":
            raise RuntimeError(f"the page threw: {outcome.get('description')}")
        return outcome.get("value")

    def wait_for(self, expression: str, timeout: float = 25.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.script(f"return Boolean({expression});"):
                return True
            time.sleep(0.2)
        return False

    def screenshot(self, target: Path) -> int:
        result = self.call("Page.captureScreenshot", format="png")
        data = base64.b64decode(result["data"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return len(data)


def target_url(port: int, timeout: float = 25.0) -> str:
    """The debugging socket for a fresh tab. Chromium 111+ answers `PUT` and refuses `GET`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for method in ("PUT", "GET"):
            try:
                request = urllib.request.Request(f"http://127.0.0.1:{port}/json/new", method=method)
                with urllib.request.urlopen(request, timeout=5) as answer:
                    return json.loads(answer.read())["webSocketDebuggerUrl"]
            except urllib.error.HTTPError as refusal:
                if refusal.code not in (405, 501):
                    break
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, KeyError):
                break
        time.sleep(0.3)
    raise TimeoutError("the browser never offered a debugging target")


def set_field(page: Page, selector: str, value: str) -> None:
    """Put a value into a React-controlled field the way a person would.

    `field.value = x` does not work on a controlled input: React's own value tracker sees no change and
    `onChange` never fires. The native setter plus a bubbling `input` event is the documented way past that,
    and it is what this does.
    """
    page.script(
        f"const field = document.querySelector('{selector}');"
        "const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;"
        f"setter.call(field, {json.dumps(value)});"
        "field.dispatchEvent(new Event('input', { bubbles: true }));"
        "return true;"
    )


def choose(page: Page, selector: str, value: str) -> None:
    """Pick an option in a React-controlled select."""
    page.script(
        f"const field = document.querySelector('{selector}');"
        f"field.value = {json.dumps(value)};"
        "field.dispatchEvent(new Event('change', { bubbles: true }));"
        "return true;"
    )


def main() -> int:
    health = engine()
    if health is None:
        print(f"nothing answering as TheNormalizer on {BACKEND}. Start it first:")
        print(r"  venv\Scripts\python.exe backend\server.py")
        return 1
    print(f"engine:  {health['product']} {health['version']} — {health.get('versionLine', '')}")

    binary = browser()
    if binary is None:
        print("no Chromium-based browser was found, so nothing was rendered.")
        return 1
    print(f"browser: {binary}")

    try:
        import websocket
    except ImportError:
        print("websocket-client is not installed, so the page was not driven.")
        print("  venv\\Scripts\\python.exe -m pip install websocket-client")
        return 1

    profile = Path(tempfile.mkdtemp(prefix="normalizer-browser-"))
    port = free_port()
    running = subprocess.Popen(
        [
            str(binary),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--force-device-scale-factor=1",
            "--window-size=1600,1000",
            # Chromium 111+ refuses a DevTools WebSocket whose handshake carries an `Origin` it was not
            # told to allow, and a driver that does not send one is a driver that does not connect. The
            # flag is scoped to the debugging port, which is only listening on loopback and only for the
            # life of this script.
            "--remote-allow-origins=*",
            "--remote-debugging-port=" + str(port),
            "--user-data-dir=" + str(profile),
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        connection = websocket.create_connection(target_url(port), timeout=45)
        page = Page(connection)
        page.call("Runtime.enable")
        page.call("Page.enable")
        page.call(
            "Emulation.setDeviceMetricsOverride",
            width=1600,
            height=1000,
            deviceScaleFactor=1,
            mobile=False,
        )
        page.call("Page.navigate", url=f"{BACKEND}/")
        mounted = page.wait_for("document.querySelector('.titlebar__product')")

        print()
        # ---- It loaded, from the engine's own origin, and React mounted -------------------
        check("React mounts on the page the engine serves", mounted, page.script("return location.href;"))
        check(
            "the product names itself",
            page.script("return document.querySelector('.titlebar__product').textContent;")
            == "TheNormalizer",
            page.script("return document.querySelector('.titlebar__product').textContent;"),
        )
        check(
            "the empty queue says what the product does and what to do",
            "Nothing queued" in page.script("return document.querySelector('.queue__empty').textContent;"),
            "the empty state is rendered",
        )

        # ---- The engine's health, which is a fetch and not markup -------------------------
        healthy = page.wait_for(
            "document.querySelector('.footerline').textContent.includes('ffmpeg ready')"
        )
        footer = page.script("return document.querySelector('.footerline').textContent;")
        check("the footer got its answer from the engine", healthy, footer.replace("\n", " ").strip())
        check(
            "the sound-file note is written from what this machine can do",
            "uncompressed 16-bit WAV" in page.script("return document.body.textContent;"),
            "the note is rendered, which needs the health answer",
        )

        # ---- The settings ------------------------------------------------------------------
        check(
            "the target is the family's default",
            page.script("return document.querySelector('#target').value;") == "-6.0",
            page.script("return document.querySelector('#target').value;"),
        )
        check(
            "the strategy that reaches any target is the default",
            page.script("return document.querySelector('#strategy').value;") == "gain",
            page.script("return document.querySelector('#strategy').value;"),
        )
        check(
            "the chain's two figures are disabled while the strategy has no chain",
            page.script(
                "return document.querySelector('#makeup').disabled "
                "&& document.querySelector('#ceiling').disabled;"
            ),
            "both disabled: the window is saying they do nothing on this strategy",
        )

        choose(page, "#strategy", "chain")
        enabled = page.wait_for("!document.querySelector('#makeup').disabled")
        makeup = page.script("return document.querySelector('#makeup').value;")
        ceiling = page.script("return document.querySelector('#ceiling').value;")
        check(
            "choosing the operator's chain enables them, at the operator's own values",
            enabled and makeup == "12.0" and ceiling == "-6.0",
            f"make-up {makeup} dB into a {ceiling} dBFS limiter",
        )
        check(
            "the note says what the chain is",
            "limiter" in page.script("return document.body.textContent;"),
            "the strategy's own sentence is rendered",
        )

        # A target the limiter cannot reach is warned about in the prose, before anything is pressed.
        set_field(page, "#target", "-1.0")
        warned = page.wait_for("Boolean(document.querySelector('.field-row__prose--warn'))", timeout=8.0)
        check(
            "a target above the limiter's working level is flagged before the run",
            warned,
            page.script(
                "const w = document.querySelector('.field-row__prose--warn');"
                "return w ? w.textContent.slice(0, 130) : 'no warning on the page';"
            ),
        )
        set_field(page, "#target", "-6.0")
        choose(page, "#strategy", "gain")

        # ---- The run control, and the layout ----------------------------------------------
        check(
            "the run control is present and disabled with nothing queued",
            page.script("return document.querySelector('.btn--primary').disabled;"),
            "there is nothing to press it for, so it is disabled rather than absent",
        )
        layout = page.script(
            "const d = document.documentElement;"
            "const wells = document.querySelectorAll('.queue, .log, .panel-body');"
            "const controls = document.querySelectorAll('.btn, .field-row__label, #target, #strategy');"
            "const past = [...controls].filter(e => e.getBoundingClientRect().right > window.innerWidth + 1);"
            "return {"
            "  x: d.scrollWidth - window.innerWidth,"
            "  y: d.scrollHeight - window.innerHeight,"
            "  wells: wells.length,"
            "  past: past.length,"
            "};"
        )
        check(
            "the frame fits its own window and nothing runs off the right edge",
            layout["x"] <= 0 and layout["y"] <= 0 and layout["past"] == 0,
            f"horizontal overflow {layout['x']} px, vertical {layout['y']} px, "
            f"{layout['past']} controls past the edge, {layout['wells']} scrollable wells",
        )
        check("the page reported no error", not page.console, "; ".join(page.console) or "none")

        SHOTS.mkdir(parents=True, exist_ok=True)
        size = page.screenshot(SHOTS / "01-empty.png")
        print(f"\n  screenshot: docs/shots/01-empty.png ({size:,} bytes)")

        connection.close()
    except Exception as failure:  # noqa: BLE001 - reported as a failed claim, not as a traceback
        check("the browser session completed", False, f"{type(failure).__name__}: {failure}")
    finally:
        running.terminate()
        try:
            running.wait(timeout=10)
        except subprocess.TimeoutExpired:
            running.kill()
        shutil.rmtree(profile, ignore_errors=True)

    if FAILURES:
        print(f"\n{len(FAILURES)} claim(s) did not hold:")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("\nevery claim held")
    return 0


if __name__ == "__main__":
    sys.exit(main())
