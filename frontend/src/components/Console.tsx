/**
 * The run's progress, and everything it has said.
 *
 * ## Why the log is a panel in the left column rather than a band across the bottom
 *
 * It was a band, and it had 128 px of a 720 px window — of which 30 was a progress strip of its own, so a
 * run's log got about **90 px: four lines**. The whole point of this console is that it carries every stage,
 * every exact command line and every measurement a run makes, and a log a run outgrows in its first second
 * is a log nobody reads. Under the settings it has the height the settings do not use.
 *
 * ## Why the progress figures are in the panel's header
 *
 * Because a header strip is exactly what they need and the panel already has one. A separate 28 px band
 * above the footer was a **third** strip in a 720 px window — title bar, log header, progress strip, footer —
 * and it was the one carrying the least information. The figures are the run's position, so they belong on
 * the log's own header, where the log is.
 *
 * The header keeps the same shape as every other zone's: a title on the left, and the state of the thing it
 * titles on the right.
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
};

export function Console({ run, log, rows, elapsed, onClear }: Props) {
  const busy = run.kind === "running";
  const done = rows.filter((row) => row.stage === "done" || row.stage === "skipped").length;
  const working = rows.find((row) => row.stage === "running") ?? null;
  const fraction = working?.fraction ?? null;

  const headline = (() => {
    if (run.kind === "failed") {
      return "Stopped";
    }
    if (!busy) {
      return log.length === 0 ? "nothing yet" : `${log.length} lines`;
    }
    return working === null ? "Starting…" : (working.stage_label ?? "Working…");
  })();

  return (
    <section className="zone zone--log" aria-label="Log">
      <div className="zone__head">
        <h2 className="zone__title">Log</h2>

        <span className="log__figure">
          {/*
            A real `<progress>` where the engine knows the length, and a sweeping fill where it does not:
            `progress.fraction` is `null` when a pass cannot know how long it will take — a `volumedetect`
            pass reads a whole file to answer one question and reports no position while it does. A bar
            filling to a number nobody measured is a lie about the one thing the operator is watching.
          */}
          {busy ? (
            fraction === null ? (
              <span className="log__bar log__bar--running" role="img" aria-label="Working" />
            ) : (
              <progress
                className="log__bar"
                value={Math.round(fraction * 100)}
                max={100}
                aria-label="Progress through the file being normalized"
              />
            )
          ) : (
            <progress
              className="log__bar"
              value={rows.length === 0 ? 0 : Math.round((done / rows.length) * 100)}
              max={100}
              aria-label="Files finished"
            />
          )}
          <span className="log__count">
            {busy && fraction === null
              ? "····"
              : busy
                ? `${Math.round((fraction ?? 0) * 100)}%`
                : `${done}/${rows.length}`}
          </span>
        </span>

        <span className="zone__note">{headline}</span>

        <span className="spacer" />

        {working !== null && busy ? (
          <span className="log__figure-text" title={working.path}>
            {working.name}
          </span>
        ) : null}
        {busy ? <span className="log__figure-text">elapsed {clock(elapsed)}</span> : null}

        {/* The one action on the log, and it is here because this is the thing it clears. */}
        <button type="button" className="btn btn--ghost btn--small" onClick={onClear}>
          Clear
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
              <span className="log__text">{line.text}</span>
            </p>
          ))
        )}
      </div>
    </section>
  );
}
