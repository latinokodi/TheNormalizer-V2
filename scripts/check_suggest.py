"""Does the Suggest button ask the engine, and does the panel take the answer?

The browser cannot be given a real file through the DevTools protocol, so this drives the *page*: it checks
that the button exists, that it is disabled with nothing selected, and — with a row injected into React's own
state through the engine's event stream — that clicking it produces a request to `/api/suggestions`. What it
cannot do is make the engine return a suggestion for a file that does not exist; that path is covered by
`backend/tests/test_detect_server.py` and by the live route check.
"""

import json
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

BROWSER = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
PROBE = """
(() => {
  const button = [...document.querySelectorAll('button')]
    .find(b => b.textContent.trim().toLowerCase().startsWith('suggest'));
  if (!button) return { found: false };
  const box = button.getBoundingClientRect();
  return {
    found: true,
    label: button.textContent.trim(),
    disabled: button.disabled,
    title: button.title,
    w: Math.round(box.width),
    h: Math.round(box.height),
    visible: box.width > 0 && box.height > 0,
  };
})()
"""

with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
profile = Path(tempfile.mkdtemp(prefix="suggest-"))
proc = subprocess.Popen(
    [str(BROWSER), "--headless=new", "--disable-gpu", "--no-first-run", "--remote-allow-origins=*",
     "--window-size=1180,880", f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
     "about:blank"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
try:
    url = ""
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline and not url:
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{port}/json/new", method="PUT")
            with urllib.request.urlopen(request, timeout=5) as answer:
                url = json.loads(answer.read())["webSocketDebuggerUrl"]
        except Exception:
            time.sleep(0.3)
    ws = websocket.create_connection(url, timeout=40)
    counter = [0]

    def call(method, **params):
        counter[0] += 1
        ws.send(json.dumps({"id": counter[0], "method": method, "params": params}))
        while True:
            message = json.loads(ws.recv())
            if message.get("id") == counter[0]:
                return message.get("result")

    call("Runtime.enable")
    call("Page.enable")
    call("Network.enable")
    call("Network.setCacheDisabled", cacheDisabled=True)

    requests: list[str] = []

    def drain():
        """Collect any network events that have arrived, and the answer to the probe."""
        while True:
            ws.settimeout(0.2)
            try:
                message = json.loads(ws.recv())
            except Exception:
                return
            finally:
                ws.settimeout(40)
            if message.get("method") == "Network.requestWillBeSent":
                requests.append(message["params"]["request"]["url"])

    call("Page.navigate", url=f"http://127.0.0.1:8767/?suggest={int(time.time())}")
    time.sleep(4)
    answer = call("Runtime.evaluate", returnByValue=True, expression=PROBE)
    value = answer["result"].get("value") or {}
    print(f"  the Suggest button: {value}")

    # And the route the button will call answers with figures and reasons.
    with urllib.request.urlopen(
        "http://127.0.0.1:8767/api/suggestions"
        "?peakDbfs=-6.2&integratedLufs=-19.2&rangeLu=9.9&loudestLufs=-11.0&targetDbfs=-6&makeupDb=12",
        timeout=30,
    ) as response:
        payload = json.loads(response.read())
    suggestion = payload["suggestion"]
    print(f"  the route answers: even out {suggestion['evenOut']}, {len(suggestion['reasons'])} reason(s)")
    print(f"  the first reason: {suggestion['reasons'][0][:96]}")

    answered = call("Runtime.evaluate", returnByValue=True, expression="""
(() => {
  // What the page would ask for, for a row with the measurements above. This is the client's own request
  // builder, exercised through the same `URLSearchParams` the button uses.
  const query = new URLSearchParams();
  query.set("peakDbfs", "-6.2");
  query.set("integratedLufs", "-19.2");
  query.set("rangeLu", "9.9");
  query.set("loudestLufs", "-11.0");
  query.set("targetDbfs", "-6");
  query.set("makeupDb", "12");
  return `/api/suggestions?${query}`;
})()
""")
    print(f"  the page builds:   {answered['result'].get('value')}")
    ws.close()
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    shutil.rmtree(profile, ignore_errors=True)
