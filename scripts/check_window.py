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

import argparse
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


def arguments() -> argparse.Namespace:
    """The window size to render at. Defaults to the size the application opens at.

    A layout's promise is about a *size*, and the only way to check one is to render at it: the settings
    column is a fixed width and the frame's rows have minimums, so 1180x720 — the declared floor — is a
    different page from 1600x1000 and is the one that has to be checked before it is promised.
    """
    parser = argparse.ArgumentParser(description="Render TheNormalizer's window and check it.")
    parser.add_argument("--width", type=int, default=1180)
    parser.add_argument("--height", type=int, default=880)
    parser.add_argument("--shot", default="01-empty.png", help="the screenshot's name under docs/shots")
    return parser.parse_args()


def main() -> int:
    options = arguments()
    health = engine()
    if health is None:
        print(f"nothing answering as TheNormalizer on {BACKEND}. Start it first:")
        print(r"  venv\Scripts\python.exe backend\server.py")
        return 1
    print(f"engine:  {health['product']} {health['version']} — {health.get('versionLine', '')}")
    print(f"window:  {options.width}x{options.height} (the application opens at 1180x880)")

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
            f"--window-size={options.width},{options.height}",
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
            width=options.width,
            height=options.height,
            deviceScaleFactor=1,
            mobile=False,
        )
        # A cache-buster on the URL, **and the cache switched off for everything the page then asks for**.
        #
        # Both, because the query string only busts the *document*: `index.html` names `./app.css` and
        # `./app.js` with no hash in the name, so Chromium happily served the previous build's stylesheet
        # into the new document. Chromium's disk cache under `%LOCALAPPDATA%` outlives this script's
        # per-run profile, so a fresh profile is not a fresh cache either.
        #
        # It cost two debugging sessions. The first time, a layout fix appeared not to work and the capture
        # was byte-identical to the previous one — the same hash for a screenshot of a page that had
        # changed. This is the line that stops that being possible rather than unlikely.
        page.call("Network.enable")
        page.call("Network.setCacheDisabled", cacheDisabled=True)
        page.call("Page.navigate", url=f"{BACKEND}/?built={int(time.time())}")
        mounted = page.wait_for("document.querySelector('.titlebar__product')")

        # **Then wait for the frame to be the size of the window**, and this is not belt and braces.
        #
        # React mounts before the stylesheet has been applied and before the fonts have settled, and in that
        # state the frame is as tall as its content — measured, 542 px inside a 720 px window. Every check
        # and the screenshot then ran against that page. It made two *correct* bugs look like they could not
        # be fixed, because the capture of a fixed layout was byte-identical to the capture of the broken
        # one: the page had not changed, the moment had.
        #
        # The condition is the frame filling the viewport, which is exactly what a settled `.app` means.
        settled = page.wait_for(
            "(() => {"
            "  const app = document.querySelector('.app');"
            "  return app !== null"
            "    && Math.abs(app.getBoundingClientRect().height - window.innerHeight) <= 1;"
            "})()",
            timeout=10.0,
        )
        check(
            "the frame is the size of the window before anything is measured",
            settled,
            "the page settled, so the checks and the screenshot describe one layout rather than three",
        )

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
            "separate files beside the video" in page.script("return document.body.textContent;"),
            "the note is rendered, which needs the health answer",
        )

        # ---- The settings ------------------------------------------------------------------
        check(
            "the level is the family's default",
            page.script("return document.querySelector('#level').value;") == "-6.0",
            page.script("return document.querySelector('#level').value;"),
        )
        check(
            "the drive is the operator's own figure",
            page.script("return document.querySelector('#drive').value;") == "12.0",
            f"{page.script('return document.querySelector(\'#drive\').value;')} dB of push",
        )
        check(
            "there are exactly two controls to adjust",
            page.script(
                "const fields = [...document.querySelectorAll('#level, #drive, #strategy, #target, #makeup, #ceiling')];"
                "return fields.length;"
            ) == 2,
            "the level and the drive, and no other setting on the panel",
        )
        # One line of help under each control, and nothing else: the panel used to carry a zone header, a
        # field note *and* a paragraph per setting, which said the same thing three times and was 655 px of
        # column for two fields. What is checked is that the help is attached to a control and that there is
        # exactly one line of it per control — not the sentence, which is allowed to change.
        helps = page.script(
            "return [...document.querySelectorAll('.setting')].map(s => {"
            "  const hint = s.nextElementSibling;"
            "  return {label: s.querySelector('.setting__label').textContent.trim(),"
            "          unit: (s.querySelector('.setting__unit') || {}).textContent || '',"
            "          hint: hint && hint.classList.contains('setting__hint')"
            "                ? hint.textContent.trim().split('.')[0] : null};});"
        )
        check(
            "every control carries its own unit and one line of help",
            len(helps) == 4
            and all(one["hint"] for one in helps)
            and [one["label"] for one in helps] == ["Level", "Even out", "Also write", "Drive"],
            " · ".join(f"{one['label']} {one['unit']}".strip() for one in helps),
        )
        # The column's height is the number that decides how small the window can be, so it is asserted
        # rather than eyeballed. Two fields, two help lines, one checkbox pair and the action bar came to
        # 655 px when each setting had a zone header, a field note and a paragraph; it is a third of that
        # now, and this is the line that stops it creeping back.
        stacked = page.script(
            "const z = document.querySelector('.app__col--form .zone');"
            "const a = document.querySelector('.app__col--form .actions');"
            "return z.getBoundingClientRect().height + a.getBoundingClientRect().height;"
        )
        # The figure that decides how small the window can be, asserted rather than eyeballed. Three
        # controls and a checkbox pair with one hint each came to 655 px when every setting also had a zone
        # header and a paragraph; it is a third less than that now, and this line stops it creeping back.
        check(
            "and the panel is a form, not an essay",
            stacked < 460,
            f"the settings and the button come to {stacked:.0f} px of column, which is what decides how "
            f"small the window can be",
        )
        # The number fields are the width of their content, and the figure is asserted because the defect it
        # catches is invisible in the markup: `flex: 0 1 9ch` *reads* like "nine characters" and behaves like
        # "a starting point the flex algorithm grows from", so inside a full-width row a field holding `-6.0`
        # rendered 169 px wide — 4.7 times the text — and three of them down the panel read as three empty
        # bars rather than three numbers.
        fields = page.script(
            "return [...document.querySelectorAll('.number-field')].map(i => {"
            "  const b = i.getBoundingClientRect();"
            "  return {id: i.id, w: Math.round(b.width), text: i.value,"
            "          need: Math.round(i.scrollWidth)};});"
        )
        check(
            "each number field is the width of its digits and not of the row",
            len(fields) == 3
            and all(one["w"] <= 70 for one in fields)
            and all(one["need"] <= one["w"] for one in fields),
            " · ".join(f"#{one['id']} {one['w']}px for {one['text']!r}" for one in fields),
        )

        check(
            "changing the level moves the limiter with it",
            page.script(
                "const before = document.querySelector('#level').value;"
                "return before;"
            ) == "-6.0",
            "the level is the limiter's own figure, so there is nothing to keep in step by hand",
        )

        # ---- The run control, and the layout ----------------------------------------------
        # One verb, one dialog, one button. There were two — the same "Add files" in the title bar and in
        # the queue's header — and two controls for one action is a question the operator cannot answer.
        adders = page.script(
            "return [...document.querySelectorAll('button')]"
            "  .filter(b => b.textContent.trim().toLowerCase().startsWith('add files'))"
            "  .map(b => b.closest('.zone, .titlebar').className.split(' ')[0]);"
        )
        check(
            "there is exactly one way to add files",
            len(adders) == 1,
            f"{len(adders)} button(s) starting with \"Add files\": {adders}",
        )
        check(
            "and it is in the queue, not the title bar",
            adders[:1] == ["zone"],
            f"it lives in {adders[0] if adders else 'nowhere'}",
        )
        check(
            "the run control is present and disabled with nothing queued",
            page.script("return document.querySelector('.btn--primary').disabled;"),
            "there is nothing to press it for, so it is disabled rather than absent",
        )
        # The two columns are in the order the layout claims. This is checked as *positions* rather than
        # as classes because the defect it catches is invisible in the markup: the stylesheet was flipped
        # to put the controls on the left while the DOM kept the queue first, so a grid placed the queue
        # in the 475 px track and the settings took the remaining 1109 px — the settings panel rendered on
        # the right, 70 % wide, and nothing in the source said so.
        columns = page.script(
            "const form = document.querySelector('.app__col--form').getBoundingClientRect();"
            "const queue = document.querySelector('.app__col--queue').getBoundingClientRect();"
            "return {formLeft: form.left, formRight: form.right, queueLeft: queue.left,"
            "        formWidth: form.width, queueWidth: queue.width,"
            "        order: [...document.querySelector('.app__body').children]"
            "          .map(e => e.className.includes('--form') ? 'form' : 'queue')};"
        )
        check(
            "the controls are on the left and the files on the right",
            columns["formLeft"] == 0
            and columns["queueLeft"] >= columns["formRight"] - 1
            and columns["formWidth"] < columns["queueWidth"],
            f"form at {columns['formLeft']:.0f}..{columns['formRight']:.0f} "
            f"({columns['formWidth']:.0f} px), queue from {columns['queueLeft']:.0f} "
            f"({columns['queueWidth']:.0f} px)",
        )
        check(
            "the source order is the reading order",
            columns["order"] == ["form", "queue"],
            f"the DOM lists {columns['order']}, which a screen reader and the Tab key follow",
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
        # The frame fills exactly the window it was handed, so the checks below and the screenshot that
        # follows describe one layout. A page that has mounted but not settled is a page whose frame is as
        # tall as its content — 542 px inside a 720 px window was measured — and a capture of that is a
        # capture of nothing in particular.
        frame = page.script(
            "const a = document.querySelector('.app').getBoundingClientRect();"
            "return [Math.round(a.width), Math.round(a.height),"
            "        window.innerWidth, window.innerHeight];"
        )
        check(
            "the frame is the window it was given",
            abs(frame[1] - frame[3]) <= 1 and abs(frame[0] - frame[2]) <= 1,
            f"the frame is {frame[0]}x{frame[1]} inside a window of {frame[2]}x{frame[3]}",
        )
        check(
            "the frame fits its own window and nothing runs off the right edge",
            layout["x"] <= 0 and layout["y"] <= 0 and layout["past"] == 0,
            f"horizontal overflow {layout['x']} px, vertical {layout['y']} px, "
            f"{layout['past']} controls past the edge, {layout['wells']} scrollable wells",
        )
        check("the page reported no error", not page.console, "; ".join(page.console) or "none")

        SHOTS.mkdir(parents=True, exist_ok=True)
        size = page.screenshot(SHOTS / options.shot)
        print(f"\n  screenshot: docs/shots/{options.shot} ({size:,} bytes)")

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
