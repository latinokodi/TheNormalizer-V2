/**
 * The queue's own logic: what a request is built from, and what the reducer does with what the engine
 * publishes.
 *
 * ## Why the reducer is worth a test
 *
 * It is where the event stream meets the rows, and every one of its cases is a claim the window makes
 * about a file: "this one finished", "this one was refused", "this one is at 40 %". A reducer is a pure
 * function, so all of that can be checked without a browser, an engine or a wait — and the two cases that
 * are easy to get wrong are both here: a job id arriving for a row that has been removed, and a `null`
 * measurement meaning *silence* rather than *zero*.
 */

import { describe, expect, it } from "vitest";

import { reduce, type Action, type Window } from "../App";
import { DEFAULT_SETTINGS, requestOf, tally, type QueueRow } from "./queue";

function aRow(overrides: Partial<QueueRow> = {}): QueueRow {
  return {
    key: 1,
    path: "C:\\media\\talk.wav",
    name: "talk.wav",
    stage: "reading",
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
    ...overrides,
  };
}

function aWindow(rows: readonly QueueRow[] = []): Window {
  return { rows, run: { kind: "idle" }, log: [], batch: null, notice: null };
}

function act(state: Window, ...actions: readonly Action[]): Window {
  return actions.reduce(reduce, state);
}

describe("requestOf", () => {
  it("carries the settings and the sources", () => {
    const body = requestOf([aRow()], DEFAULT_SETTINGS);
    expect(body.sources).toEqual(["C:\\media\\talk.wav"]);
    expect(body.targetDbfs).toBe(-6.0);
    expect(body.strategy).toBe("chain");
    expect(body.audio).toEqual([]);
  });

  it("sends one number as both the level and the limiter", () => {
    // They are the same decision: the level a finished file peaks at is the level the limiter is set to.
    // Two fields for one number is two chances to disagree about it.
    const body = requestOf([aRow()], { ...DEFAULT_SETTINGS, levelDbfs: -3.0 });
    expect(body.targetDbfs).toBe(-3.0);
    expect(body.ceilingDbfs).toBe(-3.0);
  });

  it("carries the level the engine already measured, so a settings change costs no decode", () => {
    const row = aRow({
      sourceLevels: {
        peakDbfs: -27.1,
        meanDbfs: -30.1,
        peakText: "-27.10 dBFS",
        meanText: "-30.10 dBFS",
        seconds: 6,
        secondsText: "0:00:06.000",
      },
    });
    const body = requestOf([row], DEFAULT_SETTINGS);
    expect(body.peakDbfs).toBe(-27.1);
    expect(body.meanDbfs).toBe(-30.1);
  });

  it("says 'silence' rather than omitting the field, because an absent field means 'measure it'", () => {
    const row = aRow({
      sourceLevels: {
        peakDbfs: null,
        meanDbfs: null,
        peakText: "silence",
        meanText: "silence",
        seconds: 6,
        secondsText: "0:00:06.000",
      },
    });
    const body = requestOf([row], DEFAULT_SETTINGS);
    expect(body.peakDbfs).toBe("silence");
    expect("meanDbfs" in body).toBe(false);
  });

  it("only sends a named destination for a single file, because one path cannot serve several", () => {
    const one = requestOf([aRow()], DEFAULT_SETTINGS, "C:\\out\\talk.m4a");
    expect(one.output).toBe("C:\\out\\talk.m4a");
    const many = requestOf([aRow(), aRow({ key: 2 })], DEFAULT_SETTINGS, "C:\\out\\talk.m4a");
    expect("output" in many).toBe(false);
  });
});

describe("tally", () => {
  it("counts every stage, including the ones no row is in", () => {
    const counts = tally([aRow(), aRow({ key: 2, stage: "done" }), aRow({ key: 3, stage: "failed" })]);
    expect(counts.reading).toBe(1);
    expect(counts.done).toBe(1);
    expect(counts.failed).toBe(1);
    expect(counts.skipped).toBe(0);
  });
});

describe("the reducer", () => {
  it("adds rows in the order they were given", () => {
    const state = act(aWindow(), { type: "added", rows: [aRow(), aRow({ key: 2, name: "b.wav" })] });
    expect(state.rows.map((row) => row.name)).toEqual(["talk.wav", "b.wav"]);
  });

  it("puts a refusal on the row it belongs to, with the engine's own tag", () => {
    const state = act(aWindow([aRow()]), {
      type: "refused",
      key: 1,
      message: "talk.wav has no audio stream, so there is nothing to normalize",
      reason: "shape",
    });
    expect(state.rows[0]?.stage).toBe("failed");
    expect(state.rows[0]?.error).toContain("no audio stream");
    expect(state.rows[0]?.reason).toBe("shape");
  });

  it("places a job event by the index the engine listed it at", () => {
    const state = act(
      aWindow([aRow(), aRow({ key: 2, name: "b.wav" })]),
      { type: "stage", index: 1, label: "apply the dynamics chain" },
      { type: "progress", index: 1, fraction: 0.4 },
    );
    expect(state.rows[0]?.stage).toBe("reading");
    expect(state.rows[1]?.stage).toBe("running");
    expect(state.rows[1]?.stage_label).toBe("apply the dynamics chain");
    expect(state.rows[1]?.fraction).toBe(0.4);
  });

  it("does nothing at all for an index that is not a row, rather than throwing", () => {
    // A job id can arrive for a row the operator removed while the run was working. A window that threw
    // there would lose the whole queue for a frame about a file that is no longer on screen.
    const before = aWindow([aRow()]);
    const after = act(before, { type: "progress", index: 7, fraction: 0.5 });
    expect(after.rows).toEqual(before.rows);
  });

  it("keeps the plan the outcome carried, so the row shows what the run actually used", () => {
    const measured = {
      peakDbfs: -6,
      meanDbfs: -9,
      peakText: "-6.00 dBFS",
      meanText: "-9.00 dBFS",
      seconds: 6,
      secondsText: "0:00:06.000",
    };
    const outcome = {
      job: "abc",
      index: 0,
      source: "C:\\media\\talk.wav",
      output: "C:\\media\\talk - normalized.m4a",
      plan: { gainText: "+20.50 dB", output: "C:\\media\\talk - normalized.m4a" },
      elapsed: 3.2,
      verified: true,
      checks: [],
      audioWritten: [],
      outputLevels: measured,
    } as unknown as QueueRow["outcome"];
    const state = act(aWindow([aRow()]), { type: "finished", index: 0, outcome });
    expect(state.rows[0]?.stage).toBe("done");
    expect(state.rows[0]?.fraction).toBe(1);
    expect(state.rows[0]?.output).toBe("C:\\media\\talk - normalized.m4a");
    expect(state.rows[0]?.plan?.gainText).toBe("+20.50 dB");
  });

  it("puts the log newest first and bounds it", () => {
    let state = aWindow();
    for (let index = 0; index < 450; index += 1) {
      state = act(state, {
        type: "log",
        line: { at: "00:00:00", file: "talk.wav", level: "stage", text: `line ${index}` },
      });
    }
    expect(state.log.length).toBe(400);
    expect(state.log[0]?.text).toBe("line 449");
  });

  it("records the batch the rows belong to, so a late frame cannot touch another run's rows", () => {
    const state = act(aWindow([aRow()]), {
      type: "started",
      batch: "batch-1",
      at: 0,
      jobs: [{ index: 0, output: "C:\\media\\talk - normalized.m4a" }],
    });
    expect(state.batch).toBe("batch-1");
    expect(state.run.kind).toBe("running");
    expect(state.rows[0]?.stage).toBe("running");
    expect(state.rows[0]?.output).toBe("C:\\media\\talk - normalized.m4a");
  });

  it("does not cancel a row that had already finished", () => {
    // Cancelling stops the queue; it does not un-finish a file that came out verified — the report for
    // that file is still the truth about it.
    const done = act(aWindow([aRow({ stage: "done" })]), { type: "cancelled", index: null });
    expect(done.rows[0]?.stage).toBe("done");
    const waiting = act(aWindow([aRow({ stage: "planned" })]), { type: "cancelled", index: null });
    expect(waiting.rows[0]?.stage).toBe("planned");
    const working = act(aWindow([aRow({ stage: "running" })]), { type: "cancelled", index: 0 });
    expect(working.rows[0]?.stage).toBe("cancelled");
  });

  it("clears everything, including the log and the run", () => {
    const state = act(
      aWindow([aRow({ stage: "done" })]),
      { type: "log", line: { at: "00:00:00", file: "", level: "stage", text: "x" } },
      { type: "cleared" },
    );
    expect(state.rows).toEqual([]);
    expect(state.log).toEqual([]);
    expect(state.run.kind).toBe("idle");
  });
});
