/**
 * TheNormalizer's window.
 *
 * ## The shape of it
 *
 * The settings and the log on the left, the queue of files and the report for the one under the cursor on
 * the right. Two columns, three strips — the title bar, the log's own header, the footer — and the frame
 * never scrolls: see `styles/app.css` for the frame and the arithmetic behind its minimum.
 *
 * **The log is in the left column rather than across the bottom**, which is the correction to a band that
 * had 128 px of a 720 px window for a run's every command and measurement: about four lines, immediately
 * outgrown. Under the settings it has the space the settings do not use, which is several hundred pixels.
 *
 * ## The state, and why it is one reducer
 *
 * `useReducer` over one `Window` value, because a run is a state machine and four peer booleans can
 * disagree. The reducer's actions are the *events the engine publishes* plus the operator's own edits, so
 * the code that draws a row and the code that receives a measurement are reading the same vocabulary —
 * which is what stops the two drifting into different ideas of what "done" means.
 *
 * ## Why the plan is re-asked and never re-derived
 *
 * Moving the target changes what every queued file's gain will be, and the window cannot work that out: it
 * does not know what the chain did to a file, and on a `chain` run the engine does not either until it has
 * run. So a settings change marks the queue stale and one `POST /api/normalization-plans` answers for the
 * file it is asked about, with the engine's own figures. A window that computed a "preview gain" would be
 * showing the operator a number no run will use.
 */

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import {
  api,
  desktop,
  droppedPaths,
  failureReason,
  failureSentence,
  listen,
  openMedia,
  reveal,
  type EngineEvent,
  type Health,
  type LoudnessView,
  type NormalizeRequest,
  type PlanView,
} from "./api";
import { Console } from "./components/Console";
import { QueueTable } from "./components/QueueTable";
import { ReportPanel } from "./components/ReportPanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { bytes, clock, folderOf, nameOf } from "./lib/format";
import {
  DEFAULT_SETTINGS,
  requestOf,
  tally,
  type LogLine,
  type QueueRow,
  type RunState,
  type Settings,
} from "./state/queue";

/** The window's whole state. Exported so the reducer can be exercised without a browser. */
export interface Window {
  readonly rows: readonly QueueRow[];
  readonly run: RunState;
  readonly log: readonly LogLine[];
  /** The batch the events on screen belong to, so a late frame cannot touch another run's rows. */
  readonly batch: string | null;
  /** Set when the engine refused the *request* rather than a file. */
  readonly notice: string | null;
}

const EMPTY: Window = { rows: [], run: { kind: "idle" }, log: [], batch: null, notice: null };

/** One thing that happened: an event from the engine, or an edit by the operator. */
export type Action =
  | { readonly type: "added"; readonly rows: readonly QueueRow[] }
  | { readonly type: "removed"; readonly keys: readonly number[] }
  | { readonly type: "cleared" }
  | { readonly type: "read"; readonly key: number; readonly media: QueueRow["media"]; readonly levels: QueueRow["sourceLevels"]; readonly loudness: QueueRow["loudness"]; readonly output: string | null }
  | { readonly type: "loudness"; readonly index: number; readonly loudness: LoudnessView }
  | { readonly type: "refused"; readonly key: number; readonly message: string; readonly reason: string | null }
  | { readonly type: "planned"; readonly key: number; readonly plan: PlanView; readonly output: string }
  | { readonly type: "planFailed"; readonly message: string }
  | { readonly type: "started"; readonly batch: string; readonly at: number; readonly jobs: readonly { readonly index: number; readonly output: string }[] }
  | { readonly type: "log"; readonly line: Omit<LogLine, "key"> }
  | { readonly type: "stage"; readonly index: number; readonly label: string }
  | { readonly type: "progress"; readonly index: number; readonly fraction: number | null }
  | { readonly type: "finished"; readonly index: number; readonly outcome: QueueRow["outcome"] }
  | { readonly type: "jobFailed"; readonly index: number; readonly message: string; readonly reason: string | null }
  | { readonly type: "cancelled"; readonly index: number | null }
  | { readonly type: "batchFinished"; readonly done: number; readonly failed: number; readonly elapsed: number }
  | { readonly type: "notice"; readonly message: string | null };

/** A key that never repeats, so React can draw a row that was removed and added again. */
let nextKey = 1;

/**
 * A request without its named destination.
 *
 * A batch writes each file beside its own source, because one path cannot be a destination for several
 * and the engine refuses a request that says otherwise. Dropping the field is how the window says "you
 * choose" — and it is a *drop* rather than an empty string, because an empty path is a malformed field
 * rather than an absent one.
 */
function stripOutput(body: NormalizeRequest): NormalizeRequest {
  const { output: _named, ...rest } = body;
  return rest;
}

function mapRows(
  rows: readonly QueueRow[],
  index: number,
  change: (row: QueueRow) => QueueRow,
): readonly QueueRow[] {
  return rows.map((row, at) => (at === index ? change(row) : row));
}

/**
 * The reducer.
 *
 * Every `index` in an action is the **position in the queue** the engine was told, not a key: the engine
 * numbers its jobs from zero in the order the request listed them, so the position is the identity it
 * knows. The window's own keys are for React and for the operator, and the two are joined here rather
 * than being made to mean the same thing.
 */
export function reduce(state: Window, action: Action): Window {
  switch (action.type) {
    case "added":
      return { ...state, rows: [...state.rows, ...action.rows] };

    case "removed": {
      const gone = new Set(action.keys);
      return { ...state, rows: state.rows.filter((row) => !gone.has(row.key)) };
    }

    case "cleared":
      return { ...EMPTY };

    case "read":
      return {
        ...state,
        rows: state.rows.map((row) =>
          row.key === action.key
            ? {
                ...row,
                media: action.media,
                sourceLevels: action.levels,
                loudness: action.loudness,
                output: action.output,
                stage: "planned",
                error: null,
                reason: null,
              }
            : row,
        ),
      };

    case "loudness":
      // How wide this file's own dynamics are, measured during a run. Stored rather than acted on: the run's
      // level does not depend on it — the leveler in the chain is what closes a wide spread, and that is a
      // setting the operator chose before the run started. What it does is let the report say *why* a file
      // whose peak is exactly on target still has quiet parts.
      return {
        ...state,
        rows: state.rows.map((row, at) =>
          at === action.index ? { ...row, loudness: action.loudness } : row,
        ),
      };

    case "refused":
      return {
        ...state,
        rows: state.rows.map((row) =>
          row.key === action.key
            ? { ...row, stage: "failed", error: action.message, reason: action.reason }
            : row,
        ),
      };

    case "planned":
      return {
        ...state,
        rows: state.rows.map((row) =>
          row.key === action.key ? { ...row, plan: action.plan, output: action.output } : row,
        ),
      };

    case "planFailed":
      // A refused *request* is about the settings rather than about a file, so it belongs on the action
      // bar where the settings are, not on a row that is perfectly fine.
      return { ...state, notice: action.message };

    case "started":
      return {
        ...state,
        batch: action.batch,
        notice: null,
        run: { kind: "running", batch: action.batch, startedAt: action.at },
        rows: state.rows.map((row, index) => {
          const job = action.jobs.find((candidate) => candidate.index === index);
          return {
            ...row,
            stage: "running",
            fraction: null,
            stage_label: null,
            error: null,
            reason: null,
            output: job?.output ?? row.output,
          };
        }),
      };

    case "log":
      return {
        ...state,
        // Newest first, and bounded: a batch of forty files says thousands of things, and a log nobody
        // can scroll to the beginning of is a log that is never read from the beginning.
        log: [{ ...action.line, key: logKey() }, ...state.log].slice(0, 400),
      };

    case "stage":
      return {
        ...state,
        rows: mapRows(state.rows, action.index, (row) => ({
          ...row,
          stage: "running",
          stage_label: action.label,
          fraction: null,
        })),
      };

    case "progress":
      return {
        ...state,
        rows: mapRows(state.rows, action.index, (row) => ({ ...row, fraction: action.fraction })),
      };

    case "finished":
      return {
        ...state,
        rows: mapRows(state.rows, action.index, (row) => ({
          ...row,
          stage: "done",
          outcome: action.outcome,
          plan: action.outcome?.plan ?? row.plan,
          output: action.outcome?.output ?? row.output,
          fraction: 1,
          stage_label: null,
        })),
      };

    case "jobFailed":
      return {
        ...state,
        rows: mapRows(state.rows, action.index, (row) => ({
          ...row,
          stage: "failed",
          error: action.message,
          reason: action.reason,
          fraction: null,
          stage_label: null,
        })),
      };

    case "cancelled":
      return {
        ...state,
        rows: state.rows.map((row, index) =>
          action.index === null || index === action.index
            ? { ...row, stage: row.stage === "running" ? "cancelled" : row.stage, fraction: null }
            : row,
        ),
      };

    case "batchFinished":
      return {
        ...state,
        run: { kind: "done", done: action.done, failed: action.failed, elapsed: action.elapsed },
      };

    case "notice":
      return { ...state, notice: action.message };

    /* c8 ignore next */
    default: {
      const impossible: never = action;
      throw new Error(`unhandled action ${JSON.stringify(impossible)}`);
    }
  }
}

let logSequence = 1;
function logKey(): number {
  logSequence += 1;
  return logSequence;
}

/** The wall clock for a log line: the window's own, because a log is read in order and timed by eye. */
function stamp(): string {
  const now = new Date();
  return `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}:${String(
    now.getSeconds(),
  ).padStart(2, "0")}`;
}

export function App() {
  const [state, dispatch] = useReducer(reduce, EMPTY);
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  /** Set when a run is in flight, so a settings change knows to say the queue is stale. */
  const [elapsed, setElapsed] = useState(0);
  /** True while a file is being measured, so the button can say so. */
  const [measuring, setMeasuring] = useState(0);

  const rowsRef = useRef(state.rows);
  rowsRef.current = state.rows;

  /* ---- The engine's health, once, and when it changes ---------------------------------- */

  useEffect(() => {
    let stopped = false;
    void api
      .health()
      .then((answer) => {
        if (!stopped) {
          setHealth(answer);
          setHealthError(answer.error ?? null);
        }
      })
      .catch((caught: unknown) => {
        if (!stopped) {
          setHealthError(failureSentence(caught));
        }
      });
    return () => {
      stopped = true;
    };
  }, []);

  /* ---- The event stream ----------------------------------------------------------------- */

  useEffect(() => {
    const stop = listen((event: EngineEvent) => {
      switch (event.type) {
        case "batch-started":
          // The engine numbers its jobs from zero in the order the request listed the sources, and this
          // is the only event that names them — so the two maps are filled here and every later event
          // is placed by the id it carries.
          for (const job of event.jobs) {
            jobIndex.current.set(job.job, job.index);
            jobLabel.current.set(job.job, nameOf(job.source));
          }
          dispatch({
            type: "started",
            batch: event.batch,
            at: performance.now(),
            jobs: event.jobs,
          });
          return;
        case "job-started":
        case "media":
        case "measured":
        case "plan":
          // The row already has its plan: it was planned when the file was added, and the run's own plan
          // arrives again with the outcome. These four are for a client that attached mid-run.
          return;
        case "loudness":
          // How wide this file's own dynamics are — the figure that explains the file rather than one that
          // steers the run. It is *not* among the four above, because a row that was planned when the file
          // was added has no loudness yet: the measurement is taken during the run, and this is where the
          // row learns it. Dropping it here would leave the report saying "not measured" for every file.
          dispatch({ type: "loudness", index: indexOf(event.job), loudness: event.loudness });
          return;
        case "stage":
          dispatch({ type: "stage", index: indexOf(event.job), label: event.label });
          return;
        case "progress":
          dispatch({ type: "progress", index: indexOf(event.job), fraction: event.fraction });
          return;
        case "job-finished":
          dispatch({
            type: "finished",
            index: event.outcome.index,
            outcome: event.outcome,
          });
          return;
        case "job-failed":
          dispatch({
            type: "jobFailed",
            index: indexOf(event.job),
            message: event.message,
            reason: event.reason,
          });
          return;
        case "job-cancelled":
          dispatch({ type: "cancelled", index: indexOf(event.job) });
          return;
        case "batch-finished":
          dispatch({
            type: "batchFinished",
            done: event.done,
            failed: event.failed,
            elapsed: event.elapsed,
          });
          return;
        case "log":
          dispatch({
            type: "log",
            line: { at: stamp(), file: labelOf(event.job), level: event.level, text: event.text },
          });
          return;
        default:
          return;
      }
    });
    return stop;
    // The listener is stable and the job ids it reads come from a ref, so this subscribes once. A
    // re-subscribe would replay the engine's history into the log on every state change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /**
   * A job id, as the position it was listed at — and as the file it is about.
   *
   * The engine numbers its jobs from zero in the order the request listed the sources, so the position is
   * the identity it knows. The window's own keys are for React and for the operator, and the two are
   * joined by these two maps rather than being made to mean the same thing.
   */
  const jobIndex = useRef(new Map<string, number>());
  const jobLabel = useRef(new Map<string, string>());
  // `useRef`'s initial value is computed on every render and thrown away, so the two maps are made once
  // by the lazy form of `useState` — which is the one place React promises to call an initialiser once.
  const [jobs] = useState(() => ({ index: new Map<string, number>(), label: new Map<string, string>() }));
  jobIndex.current = jobs.index;
  jobLabel.current = jobs.label;

  function indexOf(job: string): number {
    return jobIndex.current.get(job) ?? -1;
  }

  function labelOf(job: string): string {
    return jobLabel.current.get(job) ?? "";
  }

  /* ---- The run's clock ------------------------------------------------------------------ */

  useEffect(() => {
    if (state.run.kind !== "running") {
      return;
    }
    const started = state.run.startedAt;
    setElapsed((performance.now() - started) / 1000);
    const timer = window.setInterval(() => setElapsed((performance.now() - started) / 1000), 500);
    return () => window.clearInterval(timer);
  }, [state.run]);

  /* ---- Adding files --------------------------------------------------------------------- */

  /**
   * Measure a file, then plan it.
   *
   * The measurement is the engine's `GET /api/probe`, and it is one decode of the sound, so it happens
   * once per file when it is added rather than on every settings change. The level travels with every
   * later request — see `requestOf`.
   */
  const measure = useCallback(async (key: number, path: string, wanted: Settings) => {
    setMeasuring((count) => count + 1);
    try {
      const probe = await api.probe(path);
      dispatch({
        type: "read",
        key,
        media: probe.media,
        levels: probe.levels,
        loudness: probe.loudness,
        output: probe.suggestedOutput,
      });
      // Planned straight away, so the row shows the level and any cautions before anything is pressed.
      // The levels the probe just measured travel with it, so the engine does not decode the file a
      // second time to answer. The level and the limiter are one number — see `Settings`.
      const answer = await api.plan({
        sources: [path],
        targetDbfs: wanted.levelDbfs,
        ceilingDbfs: wanted.levelDbfs,
        strategy: wanted.strategy,
        makeupDb: wanted.driveDb,
        trimDb: wanted.trimDb,
        audio: wanted.audio,
        peakDbfs: probe.levels.peakDbfs === null ? "silence" : probe.levels.peakDbfs,
        ...(probe.levels.meanDbfs === null ? {} : { meanDbfs: probe.levels.meanDbfs }),
      });
      dispatch({ type: "planned", key, plan: answer.plan, output: answer.plan.output });
    } catch (caught: unknown) {
      // A file the engine will not read is a fact about that file, so it goes on the row.
      dispatch({
        type: "refused",
        key,
        message: failureSentence(caught),
        reason: failureReason(caught),
      });
    } finally {
      setMeasuring((count) => Math.max(0, count - 1));
    }
  }, []);

  const add = useCallback(
    (paths: readonly string[]) => {
      const fresh = paths
        .filter((path) => path.trim() !== "")
        .map((path) => ({
          key: nextKey++,
          path,
          name: nameOf(path),
          stage: "reading" as const,
          media: null,
          sourceLevels: null,
        loudness: null,
          plan: null,
          outcome: null,
          error: null,
          reason: null,
          output: null,
          fraction: null,
          stage_label: null,
        }));
      if (fresh.length === 0) {
        return;
      }
      dispatch({ type: "added", rows: fresh });
      setSelected((current) => current ?? fresh[0]?.key ?? null);
      for (const row of fresh) {
        void measure(row.key, row.path, settings);
      }
    },
    [measure, settings],
  );

  const browse = useCallback(async () => {
    const known = rowsRef.current[0];
    const startIn = known === undefined ? "" : folderOf(known.path);
    const chosen = await openMedia("Add media to normalize", startIn);
    add(chosen);
  }, [add]);

  /* ---- A drop, from anywhere on the window -------------------------------------------------- */

  /**
   * Whether a drag is over the window right now, which is the only reason the drop veil exists.
   *
   * A drop is handled by the empty queue's own zone and by this window-wide veil, and the veil is what makes
   * dropping work *while a queue is already running*: without it, the only drop target is a panel that is not
   * on screen once there are rows, so an operator adding a file to a batch in flight would find that the
   * gesture they used a moment ago has stopped working.
   */
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    // A `dragenter`/`dragleave` pair arrives for every element a drag crosses, so the counter is what keeps
    // the veil from flickering off each time the pointer moves between two children.
    let depth = 0;
    const over = (event: DragEvent) => {
      event.preventDefault();
      depth += 1;
      setDragging(true);
    };
    const away = () => {
      depth = Math.max(0, depth - 1);
      if (depth === 0) {
        setDragging(false);
      }
    };
    const drop = async (event: DragEvent) => {
      event.preventDefault();
      depth = 0;
      setDragging(false);
      const files = Array.from(event.dataTransfer?.files ?? []);
      if (files.length > 0) {
        add(await droppedPaths(files));
      }
    };
    // The window, and not a panel: a file dropped anywhere that is not a target makes Chromium *navigate* to
    // it, which replaces this application with a video player. Dragging a video onto a window and getting the
    // video is not a feature.
    window.addEventListener("dragenter", over);
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", away);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", over);
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", away);
      window.removeEventListener("drop", drop);
    };
  }, [add]);

  /* ---- Running -------------------------------------------------------------------------- */

  const startable = useMemo(
    () => state.rows.filter((row) => row.media !== null && row.stage !== "failed"),
    [state.rows],
  );

  const start = useCallback(async () => {
    const ready = rowsRef.current.filter((row) => row.media !== null && row.stage !== "failed");
    if (ready.length === 0) {
      return;
    }
    // Every file has to be planned as part of the request, because the engine plans what it is given:
    // the levels it was measured with travel along, so no file is decoded twice.
    //
    // One request, and the sources are the whole queue in order. The levels come from the *first* row
    // only — the engine measures any file whose level was not sent, which is what `peakDbfs` being absent
    // means — so a batch of forty is measured by the engine as it plans them rather than by forty round
    // trips from here.
    // No named destination, ever: the output folder is the source folder — see `output_for` on the
    // engine side and `stripOutput` below. One request, and the sources are the whole queue in order.
    const body: NormalizeRequest = {
      ...stripOutput(requestOf([ready[0]!], settings)),
      sources: ready.map((row) => row.path),
    };
    try {
      const answer = await api.start(body);
      for (const job of answer.jobs) {
        jobIndex.current.set(job.job, job.index);
        jobLabel.current.set(job.job, nameOf(job.source));
      }
      dispatch({
        type: "started",
        batch: answer.batch,
        at: performance.now(),
        jobs: answer.jobs,
      });
    } catch (caught: unknown) {
      dispatch({ type: "notice", message: failureSentence(caught) });
    }
  }, [settings]);

  const cancel = useCallback(async () => {
    try {
      await api.cancel();
    } catch (caught: unknown) {
      dispatch({ type: "notice", message: failureSentence(caught) });
    }
  }, []);

  /* ---- Revealing ------------------------------------------------------------------------ */

  const showInExplorer = useCallback(async (path: string) => {
    try {
      await reveal(path);
    } catch (caught: unknown) {
      dispatch({ type: "notice", message: failureSentence(caught) });
    }
  }, []);

  /* ---- Keys ----------------------------------------------------------------------------- */

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "F5") {
        event.preventDefault();
        void start();
      } else if (event.key === "Escape" && state.run.kind === "running") {
        event.preventDefault();
        void cancel();
      } else if (event.key === "Delete") {
        const target = selected;
        if (target !== null) {
          event.preventDefault();
          dispatch({ type: "removed", keys: [target] });
        }
      } else if (event.key.toLowerCase() === "o" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        void browse();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [browse, cancel, selected, start, state.run.kind]);

  /* ---- What the operator is looking at --------------------------------------------------- */

  const selectedRow = selected !== null ? (state.rows.find((row) => row.key === selected) ?? null) : null;

  /* ---- The settings, and the one path that applies them ----------------------------------- */

  /**
   * Apply settings and re-plan the file the operator is looking at.
   *
   * **There is one of these on purpose.** A suggestion applies its figures through here exactly as a typed
   * edit does, so a setting that came from the engine cannot take a different route from one that was typed:
   * no second code path, and no second place for the plan on screen to disagree with the run that follows it.
   */
  const applySettings = useCallback(
    (next: Settings) => {
      setSettings(next);
      // Re-plan one file and not forty: forty is forty decodes, and the engine does that when the run starts.
      const row = selectedRow;
      if (row === null || row.media === null) {
        return;
      }
      void api
        .plan(requestOf([row], next))
        .then((answer) =>
          dispatch({ type: "planned", key: row.key, plan: answer.plan, output: answer.plan.output }),
        )
        .catch((caught: unknown) =>
          dispatch({ type: "planFailed", message: failureSentence(caught) }),
        );
    },
    [selectedRow],
  );

  /* ---- Autodetect ------------------------------------------------------------------------ */

  /**
   * Ask the engine what this file's own measurements suggest, and apply it.
   *
   * The figures travel to the engine rather than being turned into a suggestion here, and that is a decision
   * about where a rule lives: which spread needs how much evening is measured knowledge — the calibration,
   * its limits and the reasoning are in `docs/AUTODETECT.md` — and a threshold duplicated in the interface
   * would be a second place for it to be wrong. The window asks; the engine answers with figures and with
   * the reasons for them.
   *
   * Applying it goes through the same `onChangeApplied` path as a typed edit, so the selected file is
   * re-planned exactly as if the operator had entered the numbers themselves. There is no second code path
   * for "settings that came from the engine", which is what stops the two from disagreeing.
   */
  const suggestForSelected = useCallback(async () => {
    const row = selectedRow;
    if (row === null || row.loudness === null || row.sourceLevels === null) {
      return;
    }
    try {
      const answer = await api.suggest({
        peakDbfs: row.sourceLevels.peakDbfs,
        integratedLufs: row.loudness.integratedLufs,
        rangeLu: row.loudness.rangeLu,
        loudestLufs: row.loudness.loudestLufs,
        targetDbfs: settings.levelDbfs,
        makeupDb: settings.driveDb,
      });
      const suggested = answer.suggestion;
      applySettings({
        ...settings,
        levelDbfs: suggested.levelDbfs,
        levelingDb: suggested.evenOut,
        driveDb: suggested.makeupDb,
      });
      // The reasons are the point of the feature as much as the figures are: a suggestion applied silently is
      // three numbers that changed on their own.
      for (const reason of suggested.reasons) {
        dispatch({ type: "log", line: { at: clock(0), level: "stage", file: row.name, text: reason } });
      }
    } catch (caught: unknown) {
      dispatch({ type: "notice", message: failureSentence(caught) });
    }
  }, [applySettings, selectedRow, settings]);

  const counts = tally(state.rows);
  const busy = state.run.kind === "running";
  const canStart = startable.length > 0 && !busy && measuring === 0;

  /**
   * What `Start` is about to do, in the operator's own numbers.
   *
   * It is a sentence rather than a row of figures because it sits in front of the one decision in the
   * window: a person reads it and presses the button. It names the level in dBFS *and* in the unit a
   * person actually has an intuition for — "about half as loud as the loudest a file can be" is rough and
   * it is the only thing on the panel that means anything to somebody who has never worked in dBFS.
   *
   * When the button cannot be pressed the sentence says what it is waiting for, which is the same sentence
   * with a different first clause.
   */
  const stateSentence = (() => {
    if (measuring > 0) {
      return `Reading ${measuring} file${measuring === 1 ? "" : "s"}…`;
    }
    if (state.rows.length === 0) {
      return "Add the files to normalize — video or audio, several at once.";
    }
    if (startable.length === 0) {
      return "Nothing here can be normalized: every file was refused.";
    }
    const first = state.rows[0]?.plan;
    const keeps = first === undefined || first === null ? "" : first.keepsPicture ? ", picture copied" : "";
    const extras =
      settings.audio.length === 0 ? "" : `, and the sound as ${settings.audio.join(" and ")}`;
    return (
      `${startable.length} file${startable.length === 1 ? "" : "s"} out at ` +
      `${settings.levelDbfs.toFixed(1)} dBFS${keeps}, beside each original${extras}.`
    );
  })();

  return (
    <div className="app">
      <header className="titlebar">
        <span className="titlebar__mark" aria-hidden="true">
          N
        </span>
        <span className="titlebar__product">TheNormalizer</span>
        <span className="titlebar__rule" aria-hidden="true" />
        {/* The tagline is the product's own sentence and never the ffmpeg build string: an ffmpeg
            version is forty characters of a bar whose job is to say what this application is, and it
            changes with the machine rather than with the product. The footer carries the health. */}
        <span className="titlebar__tagline">peak normalization · picture copied, sound rewritten</span>
        <span className="spacer" />
        {/*
          **`Add files` lives here**, as a filled button at the right of the bar where every application puts
          the verb that starts the work.

          It has been in three places while this was worked out, and the history is the argument. First the
          title bar *and* the queue's header, both saying `Add files`, which was two controls for one action
          and a question the operator could not answer. Then the queue's header alone — and that was worse:
          a 24 px ghost button inside a panel header is not where anybody looks for the way to put a file in,
          and the report came back saying exactly that.

          It is one button, it is filled, and it is the first thing in the bar's right-hand group. The empty
          queue carries the same action at full size for the one moment the operator is certainly looking for
          it; a second *reference* to one action in the place a reader's eye already is is not the same defect
          as two buttons that both claim to be the way in.
        */}
        <button type="button" className="btn btn--primary" onClick={() => void browse()}>
          Add files…
        </button>
        <button
          type="button"
          className="btn btn--ghost"
          onClick={() => dispatch({ type: "cleared" })}
          disabled={state.rows.length === 0 || busy}
        >
          Clear
        </button>
      </header>

      {dragging ? (
        <div className="drop-veil" role="status">
          Drop the files to add them
          <span className="drop-veil__hint">video or audio, several at once</span>
        </div>
      ) : null}

      <div className="app__body">
        {/*
          The controls come first in the DOM as well as on the screen. That is not decoration: a grid
          places its children in source order, so a column reordered by CSS alone would put the *reading*
          order — the order a screen reader and the Tab key follow — out of step with the layout. Which
          is exactly what happened: the stylesheet was flipped to put the settings on the left and the
          DOM was left with the queue first, so the two disagreed and the settings rendered on the right.
        */}
        <div className="app__col app__col--form">
          <SettingsPanel
            settings={settings}
            onChangeApplied={applySettings}
            health={health}
            busy={busy}
            canSuggest={selectedRow !== null && selectedRow.loudness !== null && selectedRow.sourceLevels !== null}
            onSuggest={() => void suggestForSelected()}
          />

          <div className="actions">
            <span className="actions__state">
              {state.notice === null ? (
                stateSentence
              ) : (
                <span className="danger">{state.notice}</span>
              )}
            </span>
            <span className="spacer" />
            {busy ? (
              <button type="button" className="btn btn--big" onClick={() => void cancel()}>
                Stop
              </button>
            ) : null}
            <button
              type="button"
              className="btn btn--primary btn--big"
              onClick={() => void start()}
              disabled={!canStart}
            >
              {busy ? "Working…" : "Normalize"}
            </button>
          </div>

          <Console
            run={state.run}
            log={state.log}
            rows={state.rows}
            elapsed={elapsed}
            onClear={() => dispatch({ type: "cleared" })}
          />
        </div>

        <div className="app__col app__col--queue">
          <QueueTable
            rows={state.rows}
            selected={selectedRow?.key ?? null}
            onSelect={(key) => setSelected(selected === key ? null : key)}
            onRemove={(key) => dispatch({ type: "removed", keys: [key] })}
            onAdd={() => void browse()}
            onReveal={showInExplorer}
            counts={counts}
            measuring={measuring > 0}
            onDropPaths={(paths) => add(paths)}
          />
          <ReportPanel row={selectedRow} onReveal={showInExplorer} onClose={() => setSelected(null)} />
        </div>
      </div>


      <footer className="footerline">
        <span className="footerline__item">
          <span className={health?.aac === true ? "status status--ok" : "status status--danger"}>
            {health === null
              ? "asking"
              : health.aac
                ? "ffmpeg ready"
                : healthError === null
                  ? "no aac encoder"
                  : "engine unreachable"}
          </span>
        </span>
        <span className="footerline__sep" aria-hidden="true" />
        <span className="footerline__item">
          <span className="footerline__detail">{state.rows.length} queued</span>
          {counts.done > 0 ? <span className="footerline__detail">{counts.done} done</span> : null}
          {counts.failed > 0 ? (
            <span className="footerline__detail" style={{ color: "var(--hazard)" }}>
              {counts.failed} failed
            </span>
          ) : null}
        </span>
        <span className="footerline__sep" aria-hidden="true" />
        <span className="footerline__item">
          <span className="footerline__detail">
            {state.rows.reduce((total, row) => total + (row.media?.sizeBytes ?? 0), 0) > 0
              ? bytes(state.rows.reduce((total, row) => total + (row.media?.sizeBytes ?? 0), 0))
              : "—"}
          </span>
        </span>
        <span className="spacer" />
        <span className="footerline__notice">
          {desktop.reveal === undefined
            ? "browser preview — Explorer buttons need the desktop window"
            : healthError ?? ""}
        </span>
        <span className="footerline__sep" aria-hidden="true" />
        <span className="footerline__item">
          <span className="footerline__detail">{busy ? `elapsed ${clock(elapsed)}` : "F5 start · Esc stop"}</span>
        </span>
      </footer>
    </div>
  );
}
