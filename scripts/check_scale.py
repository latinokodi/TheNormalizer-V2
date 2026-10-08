"""Does the layout hold at a display scale factor other than 100%?

    venv\\Scripts\\python.exe scripts\\check_window.py --scale 1.25

`--scale` emulates a Windows display scale factor by rendering at that device scale, which is what a HiDPI
machine actually does to a window: the size in *device* pixels stays the same and the size in CSS pixels
divides by the factor. Every size in this project is authored in CSS pixels, so a layout that fits perfectly
at 100 % can be handed 20 % less room at 125 % and break — which is a thing no check here could see, because
they all rendered at a factor of one.

This is a checker for one question rather than a test: *at this scale, does anything overlap or clip?* It
reports the frame's CSS size and every pair of elements whose boxes intersect.
"""

from __future__ import annotations

import argparse
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

BACKEND = "http://127.0.0.1:8767"
BROWSERS = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
)

#: Everything that is a distinct thing on the panel. Overlap between two of these is a defect wherever it
#: happens; overlap between a parent and its child is not, which is why the list is leaves rather than boxes.
WATCHED = (
    ".titlebar__mark", ".titlebar__product", ".titlebar__tagline",
    ".setting__label", ".setting__unit", ".number-field", ".setting__toggle",
    ".zone__title", ".zone__note", ".btn", ".progress-figure",
)

PROBE = """
(() => {
  const selectors = %s;
  const boxes = [];
  for (const selector of selectors) {
    for (const element of document.querySelectorAll(selector)) {
      const r = element.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;
      // An element an ancestor already covers is not a second thing on the panel.
      if (boxes.some(other => other.element.contains(element))) continue;
      boxes.push({ selector, element, r,
                   text: (element.textContent || '').trim().slice(0, 22) });
    }
  }
  const overlaps = [];
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j];
      if (a.element.contains(b.element) || b.element.contains(a.element)) continue;
      const x = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left);
      const y = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
      if (x > 1 && y > 1) {
        overlaps.push({ a: a.selector + ' ' + a.text, b: b.selector + ' ' + b.text,
                        area: Math.round(x * y) });
      }
    }
  }
  const app = document.querySelector('.app').getBoundingClientRect();
  const past = [...document.querySelectorAll('.btn, .number-field, .setting__unit')]
    .filter(e => e.getBoundingClientRect().right > window.innerWidth + 1).length;
  return {
    css: [Math.round(window.innerWidth), Math.round(window.innerHeight)],
    device: [window.innerWidth * window.devicePixelRatio,
             window.innerHeight * window.devicePixelRatio],
    ratio: window.devicePixelRatio,
    app: [Math.round(app.width), Math.round(app.height)],
    past,
    overflowX: document.documentElement.scrollWidth - window.innerWidth,
    overlaps,
  };
})()
""" % (json.dumps(list(WATCHED)),)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the layout at a display scale factor.")
    parser.add_argument("--scale", type=float, default=1.25)
    parser.add_argument("--width", type=int, default=1475, help="the window's device-pixel width")
    parser.add_argument("--height", type=int, default=1100)
    # The size that actually matters: a Windows window is created in **logical** pixels and the content ends
    # up with `logical / scale` of them, so at 125 % a window declared 1180 wide hands the layout 944. That
    # is the number to check, and emulating the viewport directly is the only way to ask for it exactly.
    parser.add_argument("--css-width", type=int, default=0)
    parser.add_argument("--css-height", type=int, default=0)
    parser.add_argument("--shot", default="", help="write a PNG of this rendering")
    options = parser.parse_args()

    binary = next((one for one in BROWSERS if one.is_file()), None)
    if binary is None:
        print("no browser found")
        return 1
    import websocket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    profile = Path(tempfile.mkdtemp(prefix="scale-"))
    running = subprocess.Popen(
        [
            str(binary), "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions",
            f"--force-device-scale-factor={options.scale}", "--remote-allow-origins=*",
            f"--window-size={options.width},{options.height}", f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}", "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        socket_url = ""
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and not socket_url:
            try:
                request = urllib.request.Request(f"http://127.0.0.1:{port}/json/new", method="PUT")
                with urllib.request.urlopen(request, timeout=5) as answer:
                    socket_url = json.loads(answer.read())["webSocketDebuggerUrl"]
            except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError, KeyError):
                time.sleep(0.3)
        connection = websocket.create_connection(socket_url, timeout=45)
        counter = [0]

        def call(method, **params):
            counter[0] += 1
            connection.send(json.dumps({"id": counter[0], "method": method, "params": params}))
            while True:
                message = json.loads(connection.recv())
                if message.get("id") == counter[0]:
                    return message.get("result")

        call("Runtime.enable")
        call("Page.enable")
        call("Network.enable")
        call("Network.setCacheDisabled", cacheDisabled=True)
        if options.css_width and options.css_height:
            call("Emulation.setDeviceMetricsOverride", width=options.css_width,
                 height=options.css_height, deviceScaleFactor=options.scale, mobile=False)
        call("Page.navigate", url=f"{BACKEND}/?scale={int(time.time())}")
        time.sleep(4)

        print(f"device scale factor {options.scale}", end="")
        if options.css_width and options.css_height:
            print(f", viewport forced to {options.css_width}x{options.css_height} css px")
        else:
            print()
        answer = call("Runtime.evaluate", returnByValue=True, expression=PROBE)
        value = answer["result"].get("value") or {}
        print(f"  the browser reports  : ratio {value.get('ratio')}, "
              f"css {value.get('css')}, device {value.get('device')}")
        print(f"  the frame            : {value.get('app')} css px")
        print(f"  horizontal overflow  : {value.get('overflowX')} px")
        print(f"  controls past the edge: {value.get('past')}")

        if options.shot:
            import base64
            answer = call("Page.captureScreenshot", format="png")
            target = Path("docs/shots") / options.shot
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(answer["data"]))
            print(f"\n  screenshot: {target} ({target.stat().st_size:,} bytes)")

        overlaps = value.get("overlaps") or []
        if not overlaps:
            print("\n  nothing overlaps.")
            return 0
        print(f"\n  {len(overlaps)} overlapping pair(s):")
        for one in overlaps[:24]:
            print(f"    {one['area']:>7} px²  {one['a']!r}  ×  {one['b']!r}")
        return 1
    finally:
        running.terminate()
        try:
            running.wait(timeout=10)
        except subprocess.TimeoutExpired:
            running.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
