/**
 * The queue: every file in the batch, what it measured, and where it has got to.
 *
 * ## Why a table and not a list of cards
 *
 * A batch is forty rows that an operator scans down the *level* column looking for the one that came out
 * wrong. That is a table's job: one file per row, one figure per column, and the columns lined up so a
 * column can be read on its own. A stack of cards cannot be scanned that way, and the third one pushes
 * the ninth off the screen.
 *
 * ## What each column is for
 *
 * * **File** — the name, and under it the path, ellipsised. The name is what the operator calls it; the
 *   path is what tells two files with the same name apart.
 * * **Measured** — what the source peaks at, in the engine's own sentence.
 * * **Target** — what it will peak at. An arrow rather than a word, because a row is 28 px tall.
 * * **State** — a word in a pill, and the bar under it while that file is the one being worked on.
 * * **Reveal / Remove** — the two things an operator does to a row.
 *
 * ## Why the state is a word as well as a colour
 *
 * Roughly one man in twelve cannot separate the red from the green. A row where "failed" is only a red
 * dot is a row that lies to him, so the pill carries the word and the colour is the second signal.
 */

import { EmptyQueue } from "./EmptyQueue";
import type { QueueRow, RowStage } from "../state/queue";
import { level } from "../lib/format";

const STAGE_WORD: Record<RowStage, string> = {
  reading: "Reading",
  planned: "Ready",
  running: "Working",
  done: "Done",
  skipped: "Skipped",
  failed: "Failed",
  cancelled: "Stopped",
};

const STAGE_CLASS: Record<RowStage, string> = {
  reading: "pill pill--waiting",
  planned: "pill pill--waiting",
  running: "pill pill--running",
  done: "pill pill--done",
  skipped: "pill pill--skipped",
  failed: "pill pill--failed",
  cancelled: "pill pill--skipped",
};

interface Props {
  readonly rows: readonly QueueRow[];
  readonly selected: number | null;
  readonly onSelect: (key: number) => void;
  readonly onRemove: (key: number) => void;
  readonly onAdd: () => void;
  /** Paths handed over by a drop anywhere on the window. See `EmptyQueue`. */
  readonly onDropPaths: (paths: readonly string[]) => void;
  readonly onReveal: (path: string) => void;
  readonly counts: Record<RowStage, number>;
  readonly measuring: boolean;
}

export function QueueTable({
  rows,
  selected,
  onSelect,
  onRemove,
  onAdd,
  onDropPaths,
  onReveal,
  counts,
  measuring,
}: Props) {
  const note = (() => {
    if (rows.length === 0) {
      return "nothing queued";
    }
    const parts = [`${rows.length} file${rows.length === 1 ? "" : "s"}`];
    if (counts.done > 0) parts.push(`${counts.done} done`);
    if (counts.failed > 0) parts.push(`${counts.failed} refused`);
    if (measuring) parts.push("reading…");
    return parts.join(" · ");
  })();

  return (
    <section className="zone" aria-label="The queue">
      <div className="zone__head">
        <h2 className="zone__title">Queue</h2>
        <span className={counts.failed > 0 ? "zone__note zone__note--caution" : "zone__note"}>
          {note}
        </span>
        {/*
          The spacer and nothing else: `Add files` moved to the title bar, because it is the one action on
          this screen and a 24 px ghost button in a panel header is not where an operator looks for the way
          to put a file in. The empty state carries the same action at full size, and this is the third
          place it is reachable from — the menu-free window has no other verb.
        */}
        <span className="spacer" />
      </div>

      {rows.length === 0 ? (
        <EmptyQueue
          rows={rows}
          measuring={measuring}
          onAdd={onAdd}
          onDropPaths={onDropPaths}
        />
      ) : (
        <div className="queue">
          <table className="queue__table">
            <caption className="sr-only">
              The files queued for normalization, with what each measures and where each has got to
            </caption>
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col" style={{ textAlign: "right" }}>
                  Measured
                </th>
                <th scope="col" style={{ textAlign: "right" }}>
                  Target
                </th>
                <th scope="col">State</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const peak = row.outcome?.outputLevels?.peakDbfs ?? row.plan?.expectedPeakDbfs ?? null;
                const source = row.sourceLevels?.peakDbfs ?? null;
                const failed = row.stage === "failed" || row.outcome?.verified === false;
                return (
                  <tr
                    key={row.key}
                    className={
                      row.key === selected ? "queue__row queue__row--selected" : "queue__row"
                    }
                    onClick={() => onSelect(row.key)}
                  >
                    <td>
                      <span className="queue__name">
                        <span className="queue__file">{row.name}</span>
                        <span className="queue__path" title={row.path}>
                          {row.error ?? row.path}
                        </span>
                      </span>
                    </td>
                    <td className="queue__level">{level(source)}</td>
                    <td
                      className={
                        row.stage === "failed"
                          ? "queue__level queue__level--failed"
                          : row.stage === "done"
                            ? "queue__level queue__level--done"
                            : "queue__level queue__level--idle"
                      }
                    >
                      {row.stage === "reading" ? "…" : level(peak)}
                    </td>
                    <td>
                      <span className="queue__state">
                        <span className={STAGE_CLASS[row.stage]}>{STAGE_WORD[row.stage]}</span>
                        {row.stage === "running" ? (
                          row.fraction === null ? (
                            <progress className="queue__bar queue__bar--idle" aria-label="Working" />
                          ) : (
                            <progress
                              className="queue__bar"
                              value={Math.round(row.fraction * 100)}
                              max={100}
                              aria-label="Progress"
                            />
                          )
                        ) : null}
                      </span>
                    </td>
                    <td>
                      <span className="row">
                        {row.stage === "done" && !failed && row.output !== null ? (
                          <button
                            type="button"
                            className="btn btn--small"
                            onClick={(event) => {
                              event.stopPropagation();
                              onReveal(row.output as string);
                            }}
                          >
                            Show
                          </button>
                        ) : null}
                        <button
                          type="button"
                          className="btn btn--ghost btn--small"
                          onClick={(event) => {
                            event.stopPropagation();
                            onRemove(row.key);
                          }}
                          aria-label={`Remove ${row.name} from the queue`}
                        >
                          Remove
                        </button>
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
