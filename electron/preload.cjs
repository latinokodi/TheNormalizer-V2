/**
 * The bridge between the page and the window.
 *
 * Six functions, and that is the whole surface. Everything else the page needs is HTTP to the engine on
 * loopback, which needs no bridge — so the page holds no filesystem, no process and no node handle it
 * could be tricked into using, and the preload stays small enough to read in one go.
 *
 * Every one of these is a named channel with a fixed shape. Nothing here takes a channel name from the
 * caller, because a bridge that forwards an arbitrary channel is `ipcRenderer` with extra steps.
 */

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electronAPI", {
  /**
   * Ask for media with the operating system's own dialog. An empty list when cancelled.
   *
   * Multi-select, because the product's unit is a batch: an episode's files arrive in one gesture.
   * `title` says what is being asked for and `startIn` is the folder to open in, which the window
   * derives from the files it is already holding.
   *
   * `startIn` is a *hint* and it is treated as one: it is passed through as a string, and whether it is
   * usable at all is decided in the main process. Both arguments are passed through as strings and
   * nothing else about the dialog is caller-controlled.
   */
  openMedia: (title, startIn) =>
    ipcRenderer.invoke(
      "open-media",
      typeof title === "string" ? title : "",
      typeof startIn === "string" ? startIn : "",
    ),

  /** Where one result should be written. `null` when cancelled. */
  saveMedia: (suggested) =>
    ipcRenderer.invoke("save-media", typeof suggested === "string" ? suggested : ""),

  /** Show a file in Explorer. */
  reveal: (target) => ipcRenderer.invoke("reveal", target),

  /** Fullscreen, which the window owns because it is the window's state and not the page's. */
  toggleFullscreen: () => ipcRenderer.invoke("toggle-fullscreen"),
  isFullscreen: () => ipcRenderer.invoke("is-fullscreen"),

  /** Where the engine is listening. */
  backendUrl: () => ipcRenderer.invoke("backend-url"),
});
