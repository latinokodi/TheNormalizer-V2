"""What the rendered window is actually made of, and how big each part is.

    venv\\Scripts\\python.exe scripts\\inspect_window.py [--width 1180] [--height 720]

This is the instrument for *"why is this window this size?"* and for *"why did that stylesheet change do
nothing?"* — two questions that look identical from the outside and have completely different answers.

It reports three things, and the first is the one that matters when a measurement refuses to move:

* **which stylesheet and script the page loaded, and whether it carries the rules you just wrote.** A page
  can be the *previous* build: `index.html` names `./app.css` with no hash, so Chromium will serve a cached
  sheet into a fresh document. That produced a byte-identical screenshot of a layout that had changed, and
  cost two debugging sessions in this build;
* the size of the frame, and the viewport it was given — a page that has mounted but not settled has a frame
  as tall as its content;
* every element of the settings column and the file list, by class, with its height. That is what turns "too
  much empty space" into "two zones of 284 px around two fields of 45".

It runs against a live engine and changes nothing.
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

PROBE = """
(() => {
  const form = document.querySelector('.app__col--form');
  const rows = [];
  for (const el of form.querySelectorAll('.setting, .setting__hint, .zone, .zone__body, .actions, .field-row')) {
    const r = el.getBoundingClientRect();
    rows.push({ cls: el.className.toString(), h: Math.round(r.height),
                text: (el.textContent || '').trim().slice(0, 40) });
  }
  return {
    page: location.href,
    script: [...document.scripts].map(s => s.src).join(' '),
    sheet: [...document.styleSheets].map(s => s.href).join(' '),
    hasSettingRule: [...document.styleSheets].some(s => {
      try { return [...s.cssRules].some(r => r.cssText && r.cssText.includes('setting__hint')); }
      catch (e) { return false; }
    }),
    hasFieldRowRule: [...document.styleSheets].some(s => {
      try { return [...s.cssRules].some(r => r.cssText && r.cssText.includes('field-row')); }
      catch (e) { return false; }
    }),
    formScroll: form.scrollHeight,
    formBox: Math.round(form.getBoundingClientRect().height),
    rows,
  };
})()
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Report what the settings column is built from.")
    parser.add_argument("--width", type=int, default=1180)
    parser.add_argument("--height", type=int, default=900)
    options = parser.parse_args()

    binary = next((one for one in BROWSERS if one.is_file()), None)
    if binary is None:
        print("no browser found")
        return 1
    import websocket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    profile = Path(tempfile.mkdtemp(prefix="inspect-"))
    running = subprocess.Popen(
        [
            str(binary), "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions",
            "--disable-application-cache", "--disk-cache-size=1",
            "--force-device-scale-factor=1", "--remote-allow-origins=*",
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
        connection = websocket.create_connection(socket_url, timeout=40)
        # The one call that matters: a normal reload that bypasses the cache for **the subresources too**.
        connection.send(json.dumps({"id": 5, "method": "Network.setCacheDisabled",
                                    "params": {"cacheDisabled": True}}))
        connection.send(json.dumps({"id": 2, "method": "Page.navigate",
                                    "params": {"url": f"{BACKEND}/?i={int(time.time())}"}}))
        time.sleep(4)
        connection.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                    "params": {"expression": PROBE, "returnByValue": True}}))
        while True:
            message = json.loads(connection.recv())
            if message.get("id") == 1:
                value = message["result"]["result"].get("value") or {}
                print(f"page          : {value.get('page')}")
                print(f"stylesheet    : {value.get('sheet')}")
                print(f"has new rules : {value.get('hasSettingRule')}")
                print(f"has old rules : {value.get('hasFieldRowRule')}")
                print(f"form height   : {value.get('formBox')} px (scroll {value.get('formScroll')})\n")
                for row in value.get("rows", []):
                    print(f"  {row['h']:>4} px  {row['cls']:<24} {row['text']}")
                break
        connection.close()
    finally:
        running.terminate()
        try:
            running.wait(timeout=10)
        except subprocess.TimeoutExpired:
            running.kill()
        shutil.rmtree(profile, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
