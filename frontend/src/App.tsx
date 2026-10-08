/**
 * TheNormalizer's window.
 *
 * ## The shape of it
 *
 * A queue of files on the left, the settings that apply to all of them and the report for the one under
 * the cursor on the right, and a console across the bottom. Two columns and a band, and the frame never
 * scrolls — see `styles/app.css` for the frame and the arithmetic behind its minimum.
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
  failureReason,
  failureSentence,
  listen,
  openMedia,
  reveal,
  saveOutput,
  type EngineEvent,
  type Health,
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
  | { readonly type: "read"; readonly key: number; readonly media: QueueRow["media"]; readonly levels: QueueRow["sourceLevels"]; readonly output: string | null }
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
                output: action.output,
                stage: "planned",
                error: null,
                reason: null,
              }
            : row,
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
  /** The one path the operator has named themselves, for the single-file case. */
  const [outputOverride, setOutputOverride] = useState<string | null>(null);
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
        output: probe.suggestedOutput,
      });
      // Planned straight away, so the row shows a gain and any warnings before anything is pressed.
      const answer = await api.plan({
        sources: [path],
        targetDbfs: wanted.targetDbfs,
        strategy: wanted.strategy,
        makeupDb: wanted.makeupDb,
        ceilingDbfs: wanted.ceilingDbfs,
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
    const named = ready.length === 1 ? (outputOverride ?? undefined) : undefined;
    const combined = requestOf([ready[0]!], settings, named);
    const body: NormalizeRequest = {
      ...combined,
      sources: ready.map((row) => row.path),
    };
    try {
      const answer = await api.start(
        ready.length === 1 ? body : stripOutput(body),
      );
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
  }, [outputOverride, settings]);

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

  const selectedRow = state.rows.find((row) => row.key === selected) ?? state.rows[0] ?? null;
  const counts = tally(state.rows);
  const busy = state.run.kind === "running";
  const canStart = startable.length > 0 && !busy && measuring === 0;

  /**
   * What `Start` is about to do, in the operator's own numbers.
   *
   * It is a sentence rather than a row of figures because it sits in front of the one decision in the
   * window: a person reads "6 files · target −6.0 dBFS · sound rewritten, picture copied" and presses
   * the button. When it cannot be pressed the sentence says what it is waiting for, which is the same
   * sentence with a different first clause.
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
    const target = first === undefined || first === null ? "" : ` · target ${first.targetText}`;
    const copies = first?.keepsPicture === false ? " · sound only" : " · picture copied, sound rewritten";
    const extras =
      settings.audio.length === 0 ? "" : ` · also writing ${settings.audio.join(" and ")}`;
    return `${startable.length} file${startable.length === 1 ? "" : "s"}${target}${copies}${extras}`;
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
        <button type="button" className="btn btn--ghost" onClick={() => void browse()}>
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

      <div className="app__body">
        <div className="app__col app__col--queue">
          <QueueTable
            rows={state.rows}
            selected={selectedRow?.key ?? null}
            onSelect={setSelected}
            onRemove={(key) => dispatch({ type: "removed", keys: [key] })}
            onAdd={() => void browse()}
            onReveal={showInExplorer}
            counts={counts}
            measuring={measuring > 0}
          />
          <ReportPanel row={selectedRow} onReveal={showInExplorer} />
        </div>

        <div className="app__col app__col--form">
          <SettingsPanel
            settings={settings}
            onChange={setSettings}
            onChangeApplied={(next) => {
              setSettings(next);
              // Re-plan the file the operator is looking at, so the gain and the warnings on screen are
              // the ones the next run would use. One request, for one file: forty files is forty decodes,
              // and the engine does that when the run starts.
              const row = selectedRow;
              if (row !== null && row.media !== null) {
                void api
                  .plan(requestOf([row], next, outputOverride ?? undefined))
                  .then((answer) =>
                    dispatch({
                      type: "planned",
                      key: row.key,
                      plan: answer.plan,
                      output: answer.plan.output,
                    }),
                  )
                  .catch((caught: unknown) =>
                    dispatch({ type: "planFailed", message: failureSentence(caught) }),
                  );
              }
            }}
            health={health}
            busy={busy}
            output={outputOverride}
            onPickOutput={async () => {
              const row = selectedRow;
              const suggested = outputOverride ?? row?.output ?? "";
              const chosen = await saveOutput(suggested);
              if (chosen !== null) {
                setOutputOverride(chosen);
              }
            }}
            onClearOutput={() => setOutputOverride(null)}
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
        </div>
      </div>

      <Console
        run={state.run}
        log={state.log}
        rows={state.rows}
        elapsed={elapsed}
        onClear={() => dispatch({ type: "cleared" })}
      />

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
