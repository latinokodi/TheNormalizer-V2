/**
 * What the run did to the file under the cursor, and what it measured.
 *
 * ## Why this is one panel and not three
 *
 * The siblings put a plan and a verification in two wells, because a stitch has a *plan* — frames in and
 * out, per piece — that is worth reading before the run and a *report* that is worth reading after. A
 * normalization has one number and its consequences: the level it measured, the level it will write, and
 * then the checks that say whether it did. So it is one panel that fills in as the run goes.
 *
 * ## The three states of a check, drawn differently
 *
 * `passed`, `failed` and `not_checked` are three verdicts and they must not look alike. A check that could
 * not run is not a quieter pass: it is a hollow square and the word, and `--phosphor-faint` rather than
 * the verified green. Collapsing it into a pass is how a report stops meaning anything.
 *
 * ## Why the numbers are the engine's
 *
 * Every figure here arrived in a record the engine published. Nothing is added up, averaged or re-derived
 * — `plan.gainText` is the engine's sentence, `outcome.outputLevels.peakText` is the engine's measurement,
 * and the difference between the target and what was delivered is a field the verifier computed. A window
 * that did that arithmetic would be a second opinion, and a second opinion about a measurement is a
 * disagreement waiting to happen.
 */

import type { QueueRow } from "../state/queue";
import { bytes, signed } from "../lib/format";

interface Props {
  readonly row: QueueRow | null;
  readonly onReveal: (path: string) => void;
}

const STATUS_CLASS: Record<string, string> = {
  passed: "status status--ok",
  failed: "status status--danger",
  not_checked: "status status--idle",
};

const STATUS_WORD: Record<string, string> = {
  passed: "ok",
  failed: "failed",
  not_checked: "not checked",
};

export function ReportPanel({ row, onReveal }: Props) {
  if (row === null) {
    return (
      <section className="zone" aria-label="The selected file">
        <div className="zone__head">
          <h2 className="zone__title">The file</h2>
          <span className="zone__note">nothing selected</span>
        </div>
        <div className="panel-body">
          <p className="empty-state">
            Select a file in the queue to see what it measures, what the run will do to it, and — once it
            has run — every check the engine made against the file it wrote.
          </p>
        </div>
      </section>
    );
  }

  const plan = row.plan;
  const outcome = row.outcome;
  const media = row.media;

  return (
    <section className="zone" aria-label="The selected file">
      <div className="zone__head">
        <h2 className="zone__title">The file</h2>
        <span className="zone__note" title={row.path}>
          {row.name}
        </span>
      </div>

      <div className="panel-body">
        {/* ---- What it measured, and what it will become -------------------------------- */}

        {row.error !== null ? (
          <p className="note note--danger">{row.error}</p>
        ) : null}

        <dl className="facts">
          <dt>Source</dt>
          <dd>{media?.summary ?? "reading…"}</dd>
          <dt>Measured peak</dt>
          <dd>{row.sourceLevels?.peakText ?? "—"}</dd>
          <dt>Measured mean</dt>
          <dd>{row.sourceLevels?.meanText ?? "—"}</dd>
          <dt>Size</dt>
          <dd>{media === null ? "—" : bytes(media.sizeBytes)}</dd>
        </dl>

        {plan === null ? null : (
          <dl className="facts facts--paths">
            <dt>Writes</dt>
            <dd>{plan.output}</dd>
            <dt>Container</dt>
            <dd>
              {plan.container} <span className="figures">·</span> {plan.strategy}
            </dd>
            <dt>Gain</dt>
            <dd className={plan.gainIsMeasured ? "ok" : "warn"}>
              {plan.gainText}
              {plan.gainIsMeasured ? "" : " (planned — the run measures the codec)"}
            </dd>
            <dt>Target</dt>
            <dd>{plan.targetText}</dd>
            {plan.keepsPicture ? (
              <>
                <dt>Picture</dt>
                <dd className="ok">copied, not re-encoded</dd>
              </>
            ) : null}
          </dl>
        )}

        {/* ---- The sound it produces ------------------------------------------------------ */}

        {plan === null ? null : (
          <>
            {/* A caption over its own block, which is what `group-label` is: the settings panel used the
                field-row family and no longer has rows to label, and this was the last use of that name. */}
            <p className="group-label">What happens to the sound</p>
            <p className="graph selectable">{plan.filterGraph}</p>
          </>
        )}

        {/* ---- What the run wrote -------------------------------------------------------- */}

        {outcome === null ? null : (
          <>
            <dl className="facts">
              <dt>Delivered peak</dt>
              <dd className={outcome.verified === false ? "danger" : "ok"}>
                {outcome.outputLevels?.peakText ?? "—"}
              </dd>
              <dt>Off target by</dt>
              <dd>
                {outcome.peakErrorDb === null || outcome.peakErrorDb === undefined
                  ? "—"
                  : `${signed(outcome.peakErrorDb)} dB`}
              </dd>
              <dt>Level moved</dt>
              <dd>
                {outcome.appliedGainDb === null || outcome.appliedGainDb === undefined
                  ? "—"
                  : `${signed(outcome.appliedGainDb)} dB`}
              </dd>
              <dt>Took</dt>
              <dd>{outcome.elapsed.toFixed(1)} s</dd>
            </dl>

            {outcome.audioWritten.length > 0 ? (
              <p className="row">
                {outcome.audioWritten.map((path) => (
                  <button
                    key={path}
                    type="button"
                    className="btn btn--small"
                    onClick={() => onReveal(path)}
                  >
                    Show {path.slice(path.lastIndexOf("."))}
                  </button>
                ))}
              </p>
            ) : null}

            <p className="row">
              <button type="button" className="btn" onClick={() => onReveal(outcome.output)}>
                Show the normalized file
              </button>
            </p>
          </>
        )}

        {/* ---- The engine's checks ------------------------------------------------------- */}

        {outcome === null ? null : (
          <table className="checks">
            <caption className="sr-only">
              Every check the engine made against the file it wrote
            </caption>
            <thead>
              <tr>
                <th scope="col">Check</th>
                <th scope="col">Verdict</th>
                <th scope="col">Measured</th>
              </tr>
            </thead>
            <tbody>
              {outcome.checks.map((check) => (
                <tr key={check.check}>
                  <th scope="row" className="checks__name">
                    {check.check}
                  </th>
                  <td>
                    <span className={STATUS_CLASS[check.status] ?? "status status--idle"}>
                      {STATUS_WORD[check.status] ?? check.status}
                    </span>
                  </td>
                  <td className="checks__detail">{check.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {/* ---- What the plan said before the run ----------------------------------------- */}

        {plan === null ? null : (
          <>
            {plan.cautions.length > 0 ? (
              <ul className="remarks">
                {plan.cautions.map((caution) => (
                  <li key={caution} className="note note--warn">
                    {caution}
                  </li>
                ))}
              </ul>
            ) : null}
            {plan.notes.length > 0 ? (
              <ul className="remarks">
                {plan.notes.map((note) => (
                  <li key={note} className="note">
                    {note}
                  </li>
                ))}
              </ul>
            ) : null}
          </>
        )}
      </div>
    </section>
  );
}
