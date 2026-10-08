/**
 * The console: how far the run is, and everything it has said.
 *
 * ## Why the log is newest-first
 *
 * A run says more than fits on a screen — every stage, every exact command line, every position ffmpeg
 * reports — and the thing an operator wants when they look at it is the *last* thing, which is the thing
 * that explains why the run is where it is. Oldest-first puts that below the fold.
 *
 * ## Why the bar is indeterminate when the engine does not know the length
 *
 * `progress.fraction` is `null` when a pass cannot know how long it will take — a `volumedetect` pass
 * reads a whole file to answer one question and reports no position while it does. A bar filling to a
 * number nobody measured is a lie about the one thing the operator is watching, so it sweeps instead.
 *
 * ## Why the failure is in the band and not a dialog
 *
 * A dialog has to be dismissed before the operator can read the log, and the log is where the rest of the
 * story is. It sits between the bar and the log, wrapping, bounded to three lines so a long ffmpeg
 * argument list cannot push the log out of the band.
 */

import type { LogLine, QueueRow, RunState } from "../state/queue";
import { clock } from "../lib/format";

interface Props {
  readonly run: RunState;
  readonly log: readonly LogLine[];
  readonly rows: readonly QueueRow[];
  readonly elapsed: number;
  readonly onClear: () => void;
}

/** The engine's log tones, as the class that draws each. Unknown levels fall to the neutral. */
const TONE: Record<string, string> = {
  command: "log__line log__line--command",
  done: "log__line log__line--done",
  error: "log__line log__line--error",
  heartbeat: "log__line log__line--heartbeat",
  stage: "log__line log__line--stage",
  log: "log__line",
  stdout: "log__line",
};

export function Console({ run, log, rows, elapsed, onClear }: Props) {
  const busy = run.kind === "running";
  /**
   * Which file the run is on, and how far through it.
   *
   * The bar is about *the file being worked on* rather than the batch, because a batch's own fraction
   * would need every file's length before the first one had been read — and the engine does not know a
   * file's length until it has probed it.
   */
  const working = rows.find((row) => row.stage === "running") ?? null;
  const done = rows.filter((row) => row.stage === "done").length;
  const fraction = working?.fraction ?? null;

  const headline = (() => {
    if (busy) {
      return working === null ? "Starting…" : (working.stage_label ?? "Working…");
    }
    if (run.kind === "done") {
      const failed = run.failed > 0 ? `, ${run.failed} failed` : "";
      return `Finished ${run.done} file${run.done === 1 ? "" : "s"}${failed} in ${run.elapsed.toFixed(1)} s`;
    }
    if (run.kind === "failed") {
      return run.message;
    }
    return rows.length === 0 ? "Nothing has run yet." : "Ready. Press Normalize.";
  })();

  return (
    <section className="console" aria-label="Progress and log">
      <div className="progress-strip">
        {busy ? (
          fraction === null ? (
            <div className="progress-bar progress-bar--running" role="img" aria-label="Working" />
          ) : (
            <progress
              className="progress-bar"
              value={Math.round(fraction * 100)}
              max={100}
              aria-label="Progress through the file being normalized"
            />
          )
        ) : (
          <progress
            className="progress-bar"
            value={rows.length === 0 ? 0 : Math.round((done / rows.length) * 100)}
            max={100}
            aria-label="Files finished"
          />
        )}
        <span className="progress-percent">
          {busy && fraction === null ? "····" : busy ? `${Math.round((fraction ?? 0) * 100)}%` : `${done}/${rows.length}`}
        </span>
        <span className="progress-status">{headline}</span>
        <span className="spacer" />
        {working !== null && busy ? (
          <span className="progress-figure" title={working.path}>
            {working.name}
          </span>
        ) : null}
        {busy ? <span className="progress-figure">elapsed {clock(elapsed)}</span> : null}
        <button type="button" className="btn btn--ghost btn--small" onClick={onClear}>
          Clear log
        </button>
      </div>

      {run.kind === "failed" ? <p className="log__failure selectable">{run.message}</p> : null}

      <div className="log">
        {log.length === 0 ? (
          <p className="log__empty">
            The run's exact commands, its measurements and every check it made appear here as they happen.
          </p>
        ) : (
          log.map((line) => (
            <p key={line.key} className={TONE[line.level] ?? "log__line"}>
              <span className="log__at">{line.at}</span>
              <span className="log__file" title={line.file}>
                {line.file}
              </span>
              <span className="log__text selectable">{line.text}</span>
            </p>
          ))
        )}
      </div>
    </section>
  );
}
