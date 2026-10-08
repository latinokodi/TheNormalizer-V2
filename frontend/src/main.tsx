import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./styles/fonts.css";
import "./styles/tokens.css";
import "./styles/global.css";
import "./styles/app.css";

/**
 * The window's entry point.
 *
 * `StrictMode` is on, and it is load-bearing rather than decorative: the run is a `useReducer` whose
 * effects subscribe to the engine's event stream, and double-invoking those effects in development is
 * what proved the subscribe is *idempotent* — the first version replayed the engine's history back into
 * the progress state on every mount, and an effect that cannot be run twice is an effect that is wrong.
 */
const container = document.getElementById("root");
if (container === null) {
  throw new Error("the page has no #root to mount into");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
