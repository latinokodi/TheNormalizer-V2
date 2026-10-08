/**
 * The window's state: the queue, the settings, and the run.
 *
 * ## Why one reducer and not four peer `useState`s
 *
 * A run is a state machine — `idle | running | done | failed` — and four independent booleans can
 * disagree: `running` true while `done` is also true is not a state, it is a bug that renders. So the run
 * is a discriminated union in one `useReducer`, and everything that reads it has to say which case it is
 * handling.
 *
 * ## Why the queue is data and the settings are state
 *
 * A queue row holds *what the engine said about a file*, and the settings hold *what the operator wants*.
 * They change for different reasons and at different rates: the operator moves a slider and every queued
 * file's plan is stale; a file finishes and only its own row changes. Keeping them apart is what makes
 * "re-plan the queue" a single sentence rather than a dependency graph.
 *
 * ## What is not here
 *
 * No arithmetic. Every level, gain and duration in this file arrived from the engine and is passed
 * through untouched — see `api.ts` for why. The only numbers this module computes are *identities*: a
 * monotonically increasing key for a new row, and a count of how many rows are in each state.
 */

import type {
  CheckView,
  LevelsView,
  MediaView,
  NormalizeRequest,
  OutcomeView,
  PlanView,
  Strategy,
} from "../api";

/** Where one file has got to. A finite union, so a `Record` keyed by it indexes without `undefined`. */
export type RowStage =
  | "reading"
  | "planned"
  | "running"
  | "done"
  | "skipped"
  | "failed"
  | "cancelled";

/**
 * One file in the queue.
 *
 * `media` and `plan` are the engine's records, held rather than re-derived. `error` is the engine's own
 * sentence for a file it would not take, and it stays on the row rather than moving to a shared error
 * bar: the row is where the operator looks immediately after adding a file, and a refusal shown anywhere
 * else is one they have to hunt for.
 */
export interface QueueRow {
  /** Stable for the life of the row, and the key React draws against. Paths are not unique: the same file
   *  can be added twice, deliberately, with two different targets. */
  readonly key: number;
  readonly path: string;
  readonly name: string;
  stage: RowStage;
  media: MediaView | null;
  /** What the file measured *before* the run: the engine's reading of the source. */
  sourceLevels: LevelsView | null;
  /** What it will be. Provisional until the run has measured the codec. */
  plan: PlanView | null;
  outcome: OutcomeView | null;
  /** The engine's sentence for a refusal, or an ffmpeg failure. */
  error: string | null;
  /** The reason tag that came with it, so the window can place it. */
  reason: string | null;
  /** Where the normalized copy goes, when the engine has said. */
  output: string | null;
  /** The share of this file's current pass that is done, or `null` when the engine does not know. */
  fraction: number | null;
  /** What the engine is doing to it right now. */
  stage_label: string | null;
}

/**
 * How the run is going. One discriminated union, because these are mutually exclusive by nature.
 */
export type RunState =
  | { readonly kind: "idle" }
  | { readonly kind: "running"; readonly batch: string; readonly startedAt: number }
  | { readonly kind: "done"; readonly done: number; readonly failed: number; readonly elapsed: number }
  | { readonly kind: "failed"; readonly message: string };

/** One line of the log, as the engine wrote it. */
export interface LogLine {
  readonly key: number;
  readonly at: string;
  /** The file the line is about, or `""` for a line about the batch. */
  readonly file: string;
  readonly level: string;
  readonly text: string;
}

/** What the operator chose. Every one of these travels with a request. */
export interface Settings {
  readonly targetDbfs: number;
  readonly strategy: Strategy;
  /** The drive into the chain, in decibels. See `NormalizeSpec.makeup_db`. */
  readonly makeupDb: number;
  /** The chain's limiter ceiling, in dBFS. */
  readonly ceilingDbfs: number;
  /** Applied after the target. For matching how loud two files feel rather than how loud they peak. */
  readonly trimDb: number;
  /** The extra sound files to write beside each master. */
  readonly audio: readonly (".wav" | ".mp3")[];
}

export const DEFAULT_SETTINGS: Settings = {
  targetDbfs: -6.0,
  strategy: "gain",
  makeupDb: 12.0,
  ceilingDbfs: -6.0,
  trimDb: 0.0,
  audio: [],
};

/**
 * The body of a request from the queue and the settings.
 *
 * One builder for the plan and the run, so the operator cannot be shown a plan for one request and get a
 * file from another. The measured levels travel with it, which is what spares the engine a decode on
 * every keystroke — see `_plan_from_body` in `server.py` for why that is not a hole in the arithmetic.
 */
export function requestOf(
  rows: readonly QueueRow[],
  settings: Settings,
  output?: string,
): NormalizeRequest {
  const first = rows[0];
  const body: NormalizeRequest = {
    sources: rows.map((row) => row.path),
    targetDbfs: settings.targetDbfs,
    strategy: settings.strategy,
    makeupDb: settings.makeupDb,
    ceilingDbfs: settings.ceilingDbfs,
    trimDb: settings.trimDb,
    audio: settings.audio,
  };
  // The named destination is the operator's own answer about *one* file, so it travels whatever has been
  // measured of it: a path is not a measurement, and dropping it because the level has not arrived yet
  // would send a run that writes somewhere else.
  const named = output === undefined || rows.length !== 1 ? {} : { output };

  if (first?.sourceLevels === null || first?.sourceLevels === undefined) {
    return { ...body, ...named };
  }
  const peak = first.sourceLevels.peakDbfs;
  const mean = first.sourceLevels.meanDbfs;
  return {
    ...body,
    ...named,
    // `"silence"` rather than `null`: the field is optional and its *absence* means "measure it yourself",
    // so a silent file has to say so in a value rather than by being omitted.
    peakDbfs: peak === null ? "silence" : peak,
    ...(mean === null ? {} : { meanDbfs: mean }),
  };
}

/** How many rows are in each state, for the header's note and the footer's count. */
export function tally(rows: readonly QueueRow[]): Record<RowStage, number> {
  const counts: Record<RowStage, number> = {
    reading: 0,
    planned: 0,
    running: 0,
    done: 0,
    skipped: 0,
    failed: 0,
    cancelled: 0,
  };
  for (const row of rows) {
    counts[row.stage] += 1;
  }
  return counts;
}

/** Every check a finished file carries, or an empty list. A convenience, not a computation. */
export function checksOf(row: QueueRow): readonly CheckView[] {
  return row.outcome?.checks ?? [];
}

/** Does this row have anything to report that failed? */
export function hasFailure(row: QueueRow): boolean {
  return checksOf(row).some((check) => check.status === "failed");
}
