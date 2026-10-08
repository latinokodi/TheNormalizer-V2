"""One question, answered by the browser: how wide is each column, and where does it start?

    venv\\Scripts\\python.exe scripts\\measure_window.py

It exists because a screenshot at a non-integer scale factor is a poor instrument for a question that a
number answers exactly, and because "the settings are on the right" was being read off one.
"""

from __future__ import annotations

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

SCRIPT = """
return {
  viewport: [window.innerWidth, window.innerHeight],
  body: document.querySelector('.app__body').getBoundingClientRect().toJSON(),
  form: document.querySelector('.app__col--form').getBoundingClientRect().toJSON(),
  queue: document.querySelector('.app__col--queue').getBoundingClientRect().toJSON(),
  titlebar: document.querySelector('.titlebar').getBoundingClientRect().toJSON(),
  setWidth: getComputedStyle(document.querySelector('.app__body')).gridTemplateColumns,
  formWidth: getComputedStyle(document.documentElement).getPropertyValue('--form-panel-width'),
};
"""


def main() -> int:
    binary = next((one for one in BROWSERS if one.is_file()), None)
    if binary is None:
        print("no browser")
        return 1
    import websocket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    profile = Path(tempfile.mkdtemp(prefix="measure-"))
    running = subprocess.Popen(
        [
            str(binary), "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions",
            "--force-device-scale-factor=1", "--remote-allow-origins=*",
            "--window-size=1600,1000", f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}", "about:blank",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
        asks = {"id": 1, "method": "Runtime.evaluate",
                "params": {"expression": f"(() => {{ {SCRIPT} }})()", "returnByValue": True}}
        connection.send(json.dumps({"id": 2, "method": "Page.navigate",
                                    "params": {"url": f"{BACKEND}/?m={int(time.time())}"}}))
        time.sleep(4)
        connection.send(json.dumps(asks))
        while True:
            message = json.loads(connection.recv())
            if message.get("id") == 1:
                value = message["result"]["result"].get("value") or {}
                for key, figured in value.items():
                    print(f"{key:>10}: {figured}")
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
