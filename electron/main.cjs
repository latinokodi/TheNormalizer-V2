/**
 * TheNormalizer's window.
 *
 * ## What this process is for
 *
 * It does five things and nothing else: it starts the Python engine, it proves the engine that answered
 * is *this* engine, it opens a window on what the engine is serving, it answers the questions a page
 * cannot answer for itself — where is a file, where should this go, show me that in Explorer, fill the
 * screen — and it makes sure nothing it started outlives it.
 *
 * Everything else is the engine's. The page is served by the engine and talks to it on that same origin,
 * so this process never has to grow a second API and the page needs no CORS.
 *
 * ## Why the page comes from the engine rather than from disk
 *
 * `loadFile` was the first attempt in the sibling product and it cannot work. A Vite build is an ES
 * module, and Chromium refuses a module script loaded from `file://` — the origin is opaque, so it fails
 * CORS before a line of the application runs. Even had it loaded, every call to the engine would have
 * been `file://` -> `http://127.0.0.1:8767`: cross-origin as well, and so needing CORS headers on every
 * response to talk to its own backend. One origin deletes both.
 *
 * ## Why the port is not a sibling's, and why answering is not enough
 *
 * There are three of these applications, and the other two bind 8765 and 8766. All three answer
 * `/api/health` and all three have a planning route, so a second application that cannot have its own
 * port is an application that silently talks to the wrong engine — a failure nobody notices until the
 * numbers are wrong. So the health check does not merely ask *whether* something answered: it requires
 * the body to name this product. A stranger on the port is reported as a stranger, not waited out for
 * thirty seconds and then called a timeout.
 *
 * ## Why the window opens at the size of its content rather than maximized
 *
 * A borderless fullscreen window has no titlebar, so it cannot be restored, minimized or closed — but
 * "maximized instead" was answering the wrong question. The question is not *how do we fill the screen
 * safely*; it is *does this interface have anything to do with a whole screen*, and this one does not. It
 * is a form and a list: the settings column is a fixed 30 % and the file list beside it, and on a
 * 2560-wide display a maximized window leaves half the screen as substrate with nothing in it.
 *
 * So it opens at `WINDOW_SIZE`, which is the size the content needs, and it stays resizable for an
 * operator who wants it larger. F11 is still offered as an explicit, reversible fullscreen.
 */

const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const path = require("path");
const fs = require("fs");
const { spawn, spawnSync } = require("child_process");

const PRODUCT = "TheNormalizer";
const TAG = "[thenormalizer]";
const BACKEND_PORT = Number(process.env.PORT || 8767);
const BACKEND = `http://127.0.0.1:${BACKEND_PORT}`;
const ROOT = path.join(__dirname, "..");

/**
 * How big the window opens, and how small it may be dragged.
 *
 * ## Why they are named pairs and not literals in a constructor
 *
 * They are the numbers in this file a person will want to change, they are quoted in `README.md`, and the
 * declared minimum in `frontend/src/styles/tokens.css` is derived against them — so they are stated once,
 * here, with the reason.
 *
 * ## How they were arrived at
 *
 * **Measured, not estimated.** `scripts/inspect_window.py` renders the page in a real browser and reports
 * what each part of the interface is actually made of, because the first version of these numbers was two
 * guesses that were both wrong: the window opened at 1600x1000 and then maximized, which filled a
 * 2560-wide screen with a panel whose content is a form, and the minimum was 1280x820 because that is what
 * the sibling product used.
 *
 *   * **1100 wide** — the settings column takes 32 % of it, which is 352 px: measured, the column's content
 *     needs 284 px plus padding, and its help lines wrap at 46 characters. The file list gets the other
 *     748 px, which is a name, a level, a state, and the report's own facts beside them.
 *   * **600 tall** — bars 60, console floor 128, and the settings need 339 to be on screen without being
 *     scrolled. That leaves the file list 200 px, which is the queue's five-row floor: a window this size
 *     is tight, and it is tight rather than broken, which is what `scripts/check_window.py` checks.
 *   * **1180x720 to open** — the same three sums with 130 px more for the file list and its report.
 */
const WINDOW_SIZE = { width: 1180, height: 720 };
const WINDOW_MINIMUM = { width: 1100, height: 600 };

/**
 * What the file dialogs offer, and it is one list for both kinds of file this product takes.
 *
 * A normalizer's input is "anything with a sound in it", which is a longer list than either sibling's
 * and includes containers that hold no picture at all. The dialog is a convenience, never a restriction:
 * Electron's `openFile` dialog shows the filtered files first and the operator can type any path, so a
 * source this list forgot is one navigation away rather than impossible.
 */
const MEDIA_EXTENSIONS = [
  // Containers with a picture.
  "mp4", "m4v", "mov", "mkv", "webm", "avi", "wmv", "flv", "ts", "m2ts", "mts", "mpg", "mpeg",
  // Sound only.
  "m4a", "mka", "wav", "mp3", "flac", "aac", "ogg", "opus", "aiff", "aif", "wma", "m4b",
];

let mainWindow = null;
let backend = null;
/** True once the application has decided to quit, so a shutdown is not reported as a fault. */
let stopping = false;
/** The last few lines the engine said. The failure page is worth much more with these in it. */
const engineStderr = [];

function rememberStderr(chunk) {
  for (const line of String(chunk).split(/\r?\n/)) {
    if (line.trim() !== "") {
      engineStderr.push(line);
    }
  }
  if (engineStderr.length > 40) {
    engineStderr.splice(0, engineStderr.length - 40);
  }
}

/** The project's own virtual environment, or the system interpreter if there is not one. */
function interpreter() {
  const inVenv = path.join(ROOT, "venv", "Scripts", "python.exe");
  return fs.existsSync(inVenv) ? inVenv : "python";
}

function startBackend() {
  const script = path.join(ROOT, "backend", "server.py");
  console.log(`${TAG} backend: ${interpreter()} ${script}`);
  backend = spawn(interpreter(), [script], {
    cwd: ROOT,
    env: { ...process.env, PORT: String(BACKEND_PORT), PYTHONIOENCODING: "utf-8" },
    stdio: ["ignore", "pipe", "pipe"],
    // No console window: a black rectangle flashing beside the application on every launch reads as a
    // fault, and there is nothing in it a person can use.
    windowsHide: true,
  });
  backend.stdout.on("data", (chunk) => process.stdout.write(`[engine] ${chunk}`));
  backend.stderr.on("data", (chunk) => {
    rememberStderr(chunk);
    process.stderr.write(`[engine] ${chunk}`);
  });

  // A spawn that cannot find an interpreter does not throw and does not exit: it emits `error`, and an
  // unhandled `error` on a ChildProcess is an uncaught exception that takes the whole application down
  // before it can draw the page that would have explained it. That is the one failure mode where the
  // tool that reports failures is the thing that failed.
  backend.on("error", (failure) => {
    rememberStderr(`${failure.name}: ${failure.message}`);
    console.error(`${TAG} the engine could not be started: ${failure.message}`);
    backend = null;
    if (mainWindow !== null) {
      mainWindow.webContents.send("backend:down", { code: null, stderr: stderrTail() });
    }
  });

  backend.on("close", (code) => {
    // A backend killed by `stopBackend` reports a null code, because a signal is not an exit status.
    // Saying "exited with null" during a normal quit reads as a crash, and a log that cries wolf on
    // every clean shutdown is a log nobody reads on the one that matters.
    console.log(
      stopping
        ? `${TAG} backend stopped`
        : `${TAG} backend exited unexpectedly (code ${code}); the window cannot normalize anything ` +
            `until it is restarted`,
    );
    backend = null;
    if (!stopping && mainWindow !== null) {
      mainWindow.webContents.send("backend:down", { code, stderr: stderrTail() });
    }
  });
}

function stderrTail() {
  return engineStderr.slice(-40).join("\n");
}

/**
 * Stop the engine, and everything the engine started.
 *
 * `child.kill()` terminates the Python process and nothing else. The engine runs ffmpeg as a child of
 * its own, and on Windows a killed parent does not take its children with it — so an orphaned ffmpeg
 * survives with the output file still open, holding a lock on it. The next run then fails with a message
 * about the destination rather than about the process still writing to it. `taskkill /T` walks the tree.
 */
function stopBackend() {
  stopping = true;
  if (backend === null) {
    return;
  }
  const pid = backend.pid;
  backend = null;
  if (pid === undefined) {
    return;
  }
  try {
    spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], { windowsHide: true });
  } catch (failure) {
    console.error(`${TAG} could not stop the engine tree: ${failure.message}`);
  }
}

/**
 * Wait until the engine answers, and is this engine.
 *
 * Condition-based waiting rather than a sleep: a fixed delay is either slower than it needs to be or
 * shorter than it needs to be, and which one it is depends on the machine.
 *
 * The return value says *why* it gave up, not merely that it did. "Nothing answered" and "something else
 * is on the port" are different problems with different fixes, and a page that reports both as a timeout
 * sends the operator to the wrong one.
 */
async function waitForBackend(timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let last = "nothing answered";
  while (Date.now() < deadline) {
    try {
      const answer = await fetch(`${BACKEND}/api/health`);
      if (answer.ok) {
        const body = await answer.json();
        if (body && body.product === PRODUCT) {
          return { ok: true };
        }
        return {
          ok: false,
          reason:
            `Something is listening on port ${BACKEND_PORT} but it is not ${PRODUCT} ` +
            `(it calls itself ${JSON.stringify(body && body.product)}). ` +
            `Close it, or start this application with a different PORT.`,
        };
      }
      last = `the engine answered HTTP ${answer.status}`;
    } catch (failure) {
      // Not listening yet. That is the expected answer for the first few attempts.
      last = `${failure.name}: ${failure.message}`;
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  return {
    ok: false,
    reason:
      `Nothing answering as ${PRODUCT} on <code>${BACKEND}/api/health</code> after 30 seconds. ` +
      `The interpreter used was <code>${interpreter()}</code>. Last attempt: ${last}.`,
  };
}

/**
 * A window with a reason in it, for when the engine never came up.
 *
 * Written to a file rather than handed over as a `data:` URL. A `data:` origin is opaque, and the preload
 * bridge is attached to the window rather than the origin — but an opaque origin is still a document
 * that cannot load anything else, cannot be inspected usefully, and behaves differently from the page it
 * is standing in for. A file on disk has none of those properties and costs one write.
 */
function failurePage(detail) {
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>${PRODUCT} - the engine did not start</title><style>
body{margin:0;display:grid;place-items:center;height:100vh;background:#0d1013;color:#e9edf1;
font-family:"Inter","Segoe UI",system-ui,sans-serif}
main{max-width:52rem;padding:2rem}h1{font-size:1.15rem;font-weight:600;margin:0 0 .75rem}
p{margin:0 0 .6rem;color:#9da5ad;line-height:1.6}
code{font-family:"JetBrains Mono",Consolas,monospace;color:#e9edf1}
pre{background:#12161a;border:1px solid #2b333b;padding:.75rem;overflow:auto;max-height:14rem;
color:#b6bfc9;font-family:"JetBrains Mono",Consolas,monospace;font-size:.8rem;line-height:1.5}
</style></head><body><main><h1>The engine did not start</h1>
<p>${detail}</p>
<p>Run <code>start.bat</code> from the project folder to see the full error.</p>
<p>The last thing the engine said:</p><pre>${escapeHtml(stderrTail() || "(nothing)")}</pre>
</main></body></html>`;

  const target = path.join(app.getPath("temp"), "thenormalizer-failure.html");
  try {
    fs.writeFileSync(target, html, "utf8");
    return target;
  } catch (failure) {
    console.error(`${TAG} could not write the failure page: ${failure.message}`);
    return null;
  }
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function createWindow(engine) {
  mainWindow = new BrowserWindow({
    /*
     * The size the interface actually needs, and no more.
     *
     * The window used to open at 1600x1000 and then **maximize**, which filled a 2560-wide screen with a
     * panel whose content is 1180 wide — the file list stretched, the settings column did not, and the
     * right half of the screen was substrate with nothing in it. A desktop tool that takes the whole
     * screen has to have something to do with the whole screen, and this one does not: it is a form and a
     * list.
     *
     * `maximized` is not called at any point. The window is resizable, and dragging it larger is the
     * operator's decision rather than this application's.
     */
    width: WINDOW_SIZE.width,
    height: WINDOW_SIZE.height,
    /*
     * The floor below which the layout stops working, and it is **checked** rather than declared: a real
     * browser renders the page at exactly this size and asserts that the document does not overflow, that
     * no control runs past an edge, and that the settings column scrolls rather than clipping its own
     * Normalize button (`scripts/check_window.py --width 1100 --height 600`). The same two numbers are
     * `--frame-min-width` / `--frame-min-height` in `frontend/src/styles/tokens.css`.
     */
    minWidth: WINDOW_MINIMUM.width,
    minHeight: WINDOW_MINIMUM.height,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: "#0d1013",
    title: PRODUCT,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      // The page is a web page: it reads JSON over loopback and draws it. It has no use for a node
      // handle, so it is not given the possibility of one.
      sandbox: true,
    },
  });

  mainWindow.setMenu(null);

  // The engine's own page, or the dev server when one was asked for. `THE_NORMALIZER_DEV` is how a
  // hot-reloading session is started without a second entry point.
  const dev = process.env.THE_NORMALIZER_DEV;
  if (dev) {
    mainWindow.loadURL(dev);
  } else if (engine.ok) {
    mainWindow.loadURL(`${BACKEND}/`);
  } else {
    const page = failurePage(engine.reason);
    if (page === null) {
      mainWindow.loadURL("about:blank");
    } else {
      mainWindow.loadFile(page);
    }
  }

  // A window that can be navigated by the page it is displaying is a window that can be navigated to
  // somewhere else entirely. Only the engine (or the dev server) is a place this window belongs.
  const allowed = [BACKEND, dev].filter((value) => typeof value === "string" && value.length > 0);
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!allowed.some((origin) => url.startsWith(origin))) {
      event.preventDefault();
      console.error(`${TAG} refused a navigation to ${url}`);
    }
  });

  // Nothing in this application opens a second window. A link in content the operator loaded or pasted
  // is the ordinary way a desktop tool gets used as a browser, so it is refused and handed to the
  // operating system instead — and only when it is https, so a `file:` or a custom scheme cannot be used
  // to launch something.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) {
      void shell.openExternal(url);
    }
    return { action: "deny" };
  });

  mainWindow.webContents.on("did-fail-load", (_event, code, description) => {
    console.error(`${TAG} the interface did not load: ${code} ${description}`);
  });
  mainWindow.webContents.on("did-finish-load", () => {
    console.log(`${TAG} the interface loaded`);
  });

  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  // A renderer that reports its own fullscreen state is a renderer that can be wrong; the window knows,
  // so it is asked.
  mainWindow.on("enter-full-screen", () => mainWindow.webContents.send("fullscreen", true));
  mainWindow.on("leave-full-screen", () => mainWindow.webContents.send("fullscreen", false));
}

function bootstrap() {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  // No embedded web content. There is no `<webview>` in this interface, and a renderer that has been
  // talked into adding one does not get to attach it.
  app.on("web-contents-created", (_event, contents) => {
    contents.on("will-attach-webview", (event) => event.preventDefault());
  });

  app.whenReady().then(async () => {
    startBackend();
    const engine = await waitForBackend();
    if (!engine.ok) {
      console.error(`${TAG} the engine never answered on ${BACKEND}: ${engine.reason}`);
    }
    createWindow(engine);
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createWindow(engine);
      }
    });
  });

  app.on("window-all-closed", () => {
    stopBackend();
    app.quit();
  });
  app.on("before-quit", stopBackend);
  app.on("will-quit", stopBackend);
}

// Only one copy of the application at a time: two would fight over the port. The whole bootstrap is
// inside the branch rather than registered after it, because `app.quit()` is a request and not an
// immediate exit — anything registered after it still runs, and a second copy that quit cleanly while
// still starting an engine is worse than one that did neither.
if (!app.requestSingleInstanceLock()) {
  console.log(`${TAG} another copy is already running; this one is leaving`);
  app.quit();
} else {
  bootstrap();
}

// ---------------------------------------------------------------------------------------
// The things a page cannot do for itself
// ---------------------------------------------------------------------------------------

/**
 * The folder a file dialog should open in, or `undefined` for "wherever Windows last was".
 *
 * The window says where the file it is asking about lives, and the window is a page that has been talking
 * to the engine, so the path is *checked* rather than trusted. It must be a string that resolves to
 * something that exists; a folder is used as it is, and a file is used as the folder it is in, so a
 * caller that hands over a path rather than a directory still gets the right answer.
 *
 * **A path that does not resolve is not an error.** The dialog has a fallback of its own, and a dialog
 * that refused to open because a hint was stale — a drive that is not mounted this morning, a folder
 * deleted since the last run — would be a worse failure than one that opened somewhere else. So the
 * unusable hint is dropped and the dialog is opened without one.
 */
function folderForDialog(startIn) {
  if (typeof startIn !== "string" || startIn.trim() === "") {
    return undefined;
  }
  const resolved = path.resolve(startIn);
  try {
    return fs.statSync(resolved).isDirectory() ? resolved : path.dirname(resolved);
  } catch {
    return undefined;
  }
}

/**
 * Choose sources, several at once.
 *
 * The product's unit is a batch, so this is a multi-select dialog: an episode's worth of files is one
 * gesture rather than one per file. `startIn` is the folder the dialog opens in — the folder of the file
 * the row is already holding, so a second file from the same episode is one click away.
 *
 * `title` is passed in so the dialog says what it is asking for. It is validated as a string and nothing
 * else about the dialog is caller-controlled.
 */
ipcMain.handle("open-media", async (_event, title, startIn) => {
  const folder = folderForDialog(startIn);
  const result = await dialog.showOpenDialog(mainWindow, {
    title: typeof title === "string" && title.length > 0 ? title : "Choose media to normalize",
    // Omitted rather than set to `undefined`: the two read the same to Electron, and an options object
    // that only carries the keys it means is one that can be read without knowing that.
    ...(folder === undefined ? {} : { defaultPath: folder }),
    properties: ["openFile", "multiSelections"],
    filters: [
      { name: "Video and audio", extensions: MEDIA_EXTENSIONS },
      { name: "Everything", extensions: ["*"] },
    ],
  });
  // Cancelling is a decision and not a fault, so it is an empty list rather than an error.
  return result.canceled || result.filePaths.length === 0 ? [] : result.filePaths;
});

/**
 * Choose where one normalized file goes.
 *
 * Separate from `open-media` because the two are different questions: a save dialog returns a path that
 * does not exist yet, and an open dialog refuses one. This is offered for the single-file case; a batch
 * writes each file beside its source, because one path cannot be a destination for several files.
 */
ipcMain.handle("save-media", async (_event, suggested) => {
  const result = await dialog.showSaveDialog(mainWindow, {
    title: "Where should the normalized file go?",
    defaultPath: typeof suggested === "string" && suggested.length > 0 ? suggested : undefined,
    filters: [
      { name: "Video and audio", extensions: MEDIA_EXTENSIONS },
      { name: "Everything", extensions: ["*"] },
    ],
  });
  return result.canceled || !result.filePath ? null : result.filePath;
});

/**
 * Show a path in Explorer.
 *
 * The path comes from the renderer, which means it comes from anywhere the renderer has been. So the
 * operation is chosen by what the path *is*, and the two operations are not equally dangerous: a file is
 * revealed in its folder, which runs nothing, and only a directory is handed to the shell to open. The
 * version of this that called `shell.openPath` on anything that existed would launch whatever executable
 * the page asked it to.
 */
ipcMain.handle("reveal", async (_event, target) => {
  if (typeof target !== "string" || target.length === 0) {
    return { ok: false, error: "no path was given" };
  }
  const resolved = path.resolve(target);
  if (!fs.existsSync(resolved)) {
    return { ok: false, error: `${resolved} does not exist` };
  }
  try {
    if (fs.statSync(resolved).isDirectory()) {
      await shell.openPath(resolved);
    } else {
      shell.showItemInFolder(resolved);
    }
    return { ok: true };
  } catch (failure) {
    return { ok: false, error: failure.message };
  }
});

ipcMain.handle("toggle-fullscreen", () => {
  if (mainWindow === null) return false;
  mainWindow.setFullScreen(!mainWindow.isFullScreen());
  return mainWindow.isFullScreen();
});

ipcMain.handle("is-fullscreen", () => (mainWindow === null ? false : mainWindow.isFullScreen()));

ipcMain.handle("backend-url", () => BACKEND);
