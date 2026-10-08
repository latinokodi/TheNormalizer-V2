/**
 * Talking to the engine.
 *
 * ## What this file is
 *
 * The window is a web page and the engine is a Python process on loopback, so everything the window
 * knows about a file arrives as JSON. This module is the only place that shape is written down: the
 * records the engine publishes, the request bodies, and the event stream.
 *
 * ## Why the interface is not allowed to compute
 *
 * Every number this product shows is a measurement, and a number derived twice is a number that can
 * disagree with itself — so nothing here adds, rounds or re-derives a level. `PlanView.gainDb` is the
 * engine's arithmetic, `LevelsView.peakText` is the engine's sentence, `progress.fraction` is the
 * engine's division, and the window prints them. The only arithmetic in the whole interface is
 * presentation: a byte count as a magnitude, a path split for a save dialog's default name, and the
 * width of a bar.
 *
 * ## Why events and not polling
 *
 * A run is minutes of ffmpeg with nothing to look at. The bar has to move *while* it works, so the
 * engine pushes: every stage, every exact command line, every position ffmpeg reports. Polling would
 * either be late or be a metronome, and neither is progress.
 */

/**
 * Where the engine is.
 *
 * Three cases, in order of how much is known:
 *
 * * the window started the engine and says which port it got — that is the answer;
 * * the page was served *by* the engine, so its own origin is the engine;
 * * neither, which means the Vite dev server, whose origin is not the engine.
 *
 * `8767`, and deliberately not either sibling's: three of these applications answer `/api/health` and all
 * three have a planning route, so the port is the only thing that tells them apart on loopback.
 */
const FALLBACK_BASE = "http://127.0.0.1:8767";

function locate(): string {
  if (import.meta.env.DEV) {
    return FALLBACK_BASE;
  }
  if (window.location.protocol === "http:" || window.location.protocol === "https:") {
    return window.location.origin;
  }
  return FALLBACK_BASE;
}

let resolved: Promise<string> | null = null;

function base(): Promise<string> {
  if (resolved === null) {
    resolved = Promise.resolve(desktop.backendUrl?.() ?? locate())
      .then((found) => (found === "" ? locate() : found))
      .catch(() => locate());
  }
  return resolved;
}

/* ==========================================================================================
   What the engine measured
   ========================================================================================== */

/** The picture, as the probe reports it. `null` on a file that has none. */
export interface VideoView {
  readonly codec: string;
  readonly profile: string;
  readonly level: number;
  readonly width: number;
  readonly height: number;
  readonly pixFmt: string;
  readonly rateText: string;
  readonly frames: number;
  readonly timebase: string;
  readonly hasBFrames: boolean;
}

/** The sound, described by the engine so the window does not describe it twice. */
export interface AudioView {
  readonly codec: string;
  readonly sampleRate: number;
  readonly channels: number;
  readonly bitRate: number;
  /** `"aac 48000 Hz, 2ch"` — the engine's own sentence, shown as it was written. */
  readonly describe: string;
}

/** One file's facts, as the probe reports them. */
export interface MediaView {
  readonly path: string;
  readonly name: string;
  /** `"video"` or `"audio"` — decided by the file, not by its extension. */
  readonly kind: "video" | "audio";
  readonly container: string;
  readonly duration: number;
  readonly durationText: string;
  readonly sizeBytes: number;
  readonly summary: string;
  readonly video: VideoView | null;
  readonly audio: AudioView | null;
  readonly streams: readonly { readonly kind: string; readonly codec: string }[];
}

/**
 * A measured level.
 *
 * `peakDbfs` is `null` for digital silence, and that is a value the window has to *render*: the honest
 * form of it is the word, which is what `peakText` carries. A number would be arithmetic waiting to
 * happen on a quantity that is not there.
 */
export interface LevelsView {
  readonly peakDbfs: number | null;
  readonly meanDbfs: number | null;
  readonly peakText: string;
  readonly meanText: string;
  readonly seconds: number;
  readonly secondsText: string;
}

/** `GET /api/probe` — everything one chosen file can be asked. */
export interface ProbeView {
  readonly media: MediaView;
  readonly levels: LevelsView;
  /** Where a normalized copy would go. The engine's answer, so the row and the run agree. */
  readonly suggestedOutput: string;
}

/* ==========================================================================================
   What the run will be
   ========================================================================================== */

/** The normalizing strategies the engine names. */
export type Strategy = "gain" | "chain" | "ceiling";

/** One sound file a run would also write. */
export interface AudioFileView {
  readonly path: string;
  readonly name: string;
  readonly seconds: number;
  readonly secondsText: string;
  readonly samples: number;
  /** What the file will weigh, measured by the engine. See `plan_normalize` for why not here. */
  readonly bytes: number;
}

/**
 * The sound files a run writes beside the master.
 *
 * `taken` is which of them are already on the disk, by extension. An occupied name is **not** a refusal:
 * the master is the deliverable and these are copies of its sound, so they are skipped together with a
 * sentence and the master still goes out. It is reported because "pressing Start will also write these,
 * except that one is already there" belongs before the press rather than in the log after it.
 */
export interface AudioPlanView {
  readonly files: readonly AudioFileView[];
  readonly taken: readonly string[];
}

/** The plan: the gain, the chain and the warnings, before anything is written. */
export interface PlanView {
  readonly source: MediaView;
  readonly output: string;
  readonly outputName: string;
  readonly container: string;
  readonly strategy: Strategy;
  readonly strategyNote: string;
  readonly targetDbfs: number;
  readonly targetText: string;
  readonly trimDb: number;
  readonly makeupDb: number;
  readonly ceilingDbfs: number;
  readonly chainNote: string;
  readonly chainHelp: Readonly<Record<string, string>>;
  readonly gainDb: number;
  readonly gainText: string;
  /**
   * `false` until a run has measured the codec for this file.
   *
   * The window shows the planned figure either way and marks it provisional, because a plan that showed
   * nothing where a gain belongs would read as a plan that had failed.
   */
  readonly gainIsMeasured: boolean;
  readonly expectedPeakDbfs: number;
  readonly sourceLevels: LevelsView;
  readonly duration: number;
  readonly durationText: string;
  readonly keepsPicture: boolean;
  readonly droppedStreams: readonly string[];
  readonly notes: readonly string[];
  readonly cautions: readonly string[];
  readonly audio: AudioPlanView;
  /** The filtergraph itself, so the window can show what will be done to the sound. */
  readonly filterGraph: string;
}

/* ==========================================================================================
   What came out
   ========================================================================================== */

/**
 * One check's verdict.
 *
 * Three values, and the third is not a soft failure: `not_checked` is the honest answer for a check that
 * could not be run, and collapsing it into `passed` is how a report stops meaning anything.
 */
export type CheckStatus = "passed" | "failed" | "not_checked";

/** One check, as the engine measured it. */
export interface CheckView {
  readonly check: string;
  readonly status: CheckStatus;
  readonly detail: string;
}

/** The result of one file's run, and everything measured against it. */
export interface OutcomeView {
  readonly job: string;
  readonly index: number;
  readonly source: string;
  readonly output: string;
  readonly plan: PlanView;
  /** Seconds of wall clock. */
  readonly elapsed: number;
  /**
   * `true` when every check passed, `false` when one failed, and **`null` when verification was never
   * run**. Null must not render as a pass.
   */
  readonly verified: boolean | null;
  readonly checks: readonly CheckView[];
  /** What the finished file's sound measures. Present only when verification ran. */
  readonly outputLevels?: LevelsView;
  readonly peakErrorDb?: number | null;
  readonly appliedGainDb?: number | null;
  /**
   * The sound files this run wrote, by path. Empty when none were asked for — or when the ones that
   * would have gone there were already on the disk, which is why this is the run's answer and not a test
   * of whether those paths exist.
   */
  readonly audioWritten: readonly string[];
}

/** One reading of where a pass has got to. */
export interface TickView {
  readonly outSeconds: number;
  /** The share of *this pass* that is done, or `null` when the pass does not know its own length. */
  readonly fraction: number | null;
  readonly speed: number | null;
  readonly remaining: number | null;
  readonly frame: number | null;
  readonly size: number | null;
  readonly expectedSeconds: number | null;
}

/** The stable tag on a refusal. The window branches on this, never on the prose. */
export type FailureReason = "input" | "shape" | "output" | "busy" | "ffmpeg" | "internal";

/**
 * Anything the engine says while it works. One `data:` line of `/api/events`.
 *
 * Every event about a file carries its `job`, because a batch runs several and the window has to put
 * each line against the row it belongs to.
 */
export type EngineEvent =
  | {
      readonly type: "batch-started";
      readonly batch: string;
      readonly jobs: readonly {
        readonly job: string;
        readonly index: number;
        readonly source: string;
        readonly output: string;
      }[];
    }
  | { readonly type: "job-started"; readonly job: string; readonly source: string }
  | { readonly type: "media"; readonly job: string; readonly media: MediaView }
  | { readonly type: "measured"; readonly job: string; readonly levels: LevelsView }
  | { readonly type: "plan"; readonly job: string; readonly plan: PlanView }
  | { readonly type: "stage"; readonly job: string; readonly label: string }
  | ({ readonly type: "progress"; readonly job: string } & TickView)
  | { readonly type: "log"; readonly job: string; readonly level: string; readonly text: string }
  | {
      readonly type: "job-finished";
      readonly job: string;
      readonly ok: boolean;
      readonly outcome: OutcomeView;
    }
  | {
      readonly type: "job-failed";
      readonly job: string;
      readonly message: string;
      readonly reason: string;
    }
  | { readonly type: "job-cancelled"; readonly job: string }
  | {
      readonly type: "batch-finished";
      readonly batch: string;
      readonly done: number;
      readonly failed: number;
      readonly cancelled: number;
      readonly elapsed: number;
    };

/* ==========================================================================================
   Requests
   ========================================================================================== */

/**
 * The body of a plan or a run.
 *
 * `peakDbfs` is the source's measured level, and it travels because measuring it costs a decode: the
 * window measures once when a file is added (`GET /api/probe`) and sends the figure back. A caller that
 * leaves it out gets a measurement on the spot, which is right for a script and wrong for a slider.
 *
 * `output` is only meaningful for a single source — it is the Save dialog's answer. A batch leaves it
 * out and every file is written beside its own source.
 */
export interface NormalizeRequest {
  readonly sources: readonly string[];
  readonly targetDbfs: number;
  readonly strategy: string;
  readonly makeupDb?: number;
  readonly ceilingDbfs?: number;
  readonly trimDb?: number;
  readonly audio: readonly AudioFormat[];
  readonly peakDbfs?: number | "silence";
  readonly meanDbfs?: number;
  readonly output?: string;
}

/**
 * One extra sound file the run writes, by extension. `.mp3` is encoded from the `.wav` of the same run,
 * so the engine refuses a request for it alone.
 */
export type AudioFormat = ".wav" | ".mp3";

/** What `POST /api/normalization-plans` answers. */
export interface PlanAnswer {
  readonly plan: PlanView;
  /** Whether something is already at the path the run would write to. */
  readonly outputExists: boolean;
  /** Which of the sound files in this plan are already on the disk. */
  readonly audioTaken: readonly string[];
}

/** What `POST /api/normalizations` answers: one job per source, and where each will go. */
export interface BatchAnswer {
  readonly batch: string;
  readonly jobs: readonly {
    readonly job: string;
    readonly index: number;
    readonly source: string;
    readonly output: string;
  }[];
  readonly started: boolean;
}

/** Can this machine normalize, and with what. Small on purpose: the window shows lamps, not codec lists. */
export interface Health {
  readonly product: string;
  readonly version: string;
  readonly ffmpeg: string | null;
  readonly versionLine?: string;
  /** Whether the sound can be encoded at all. False means this machine cannot normalize anything. */
  readonly aac: boolean;
  /** Whether the MP3 export can be written. A different question from `aac`. */
  readonly libmp3lame: boolean;
  /** Whether this ffmpeg has the EBU R128 filters. Reported, not used. */
  readonly loudnorm: boolean;
  readonly error?: string;
}

/* ==========================================================================================
   Transport
   ========================================================================================== */

/**
 * Raised with the engine's own sentence, so the window never invents a reason.
 *
 * `reason` is the engine's tag for *which* refusal this is. It exists for the cases the window has to
 * place differently — a file that will not probe belongs beside the row it was added from — and telling
 * those apart by matching on prose would break the moment the prose was improved.
 */
export class ApiFailure extends Error {
  readonly reason: FailureReason | null;

  constructor(message: string, reason: FailureReason | null = null) {
    super(message);
    this.name = "ApiFailure";
    this.reason = reason;
  }
}

const REASONS: readonly string[] = ["input", "shape", "output", "busy", "ffmpeg", "internal"];

function asReason(value: unknown): FailureReason | null {
  return typeof value === "string" && REASONS.includes(value) ? (value as FailureReason) : null;
}

async function send<T>(path: string, body?: unknown, method = "POST"): Promise<T> {
  const response = await fetch(`${await base()}${path}`, {
    method: body === undefined && method === "POST" ? "GET" : method,
    ...(body === undefined
      ? {}
      : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  // `GET /api/normalizations/current` answers 204 with no body when nothing is running. Calling `.json()`
  // on that throws, and a throw here would be reported as a fault when the engine said "idle".
  if (response.status === 204) {
    return null as T;
  }
  const payload: unknown = await response.json().catch(() => ({}));
  if (!response.ok) {
    const shaped =
      typeof payload === "object" && payload !== null ? (payload as Record<string, unknown>) : {};
    const message = "error" in shaped ? String(shaped["error"]) : `${path} failed (${response.status})`;
    throw new ApiFailure(message, asReason("reason" in shaped ? shaped["reason"] : null));
  }
  return payload as T;
}

/** The engine's sentence for a thrown thing, whichever shape it arrived in. */
export function failureSentence(caught: unknown): string {
  if (caught instanceof ApiFailure) {
    return caught.message;
  }
  if (caught instanceof Error) {
    return caught.message;
  }
  return String(caught);
}

/** The reason tag for a thrown thing, or `null` when it did not come tagged. */
export function failureReason(caught: unknown): FailureReason | null {
  return caught instanceof ApiFailure ? caught.reason : null;
}

export const api = {
  health: () => send<Health>("/api/health", undefined, "GET"),

  /** Measure one source. Called when a file is added, for its facts and its level. */
  probe: (path: string) =>
    send<ProbeView>(`/api/probe?path=${encodeURIComponent(path)}`, undefined, "GET"),

  /** What the run will do, without writing anything. Called as the settings settle. */
  plan: (input: NormalizeRequest) => send<PlanAnswer>("/api/normalization-plans", input),

  /** Start a batch. Returns `201` at once; everything after that arrives on `/api/events`. */
  start: (input: NormalizeRequest) =>
    send<BatchAnswer>("/api/normalizations", input),

  /** The singleton batch, or `null` when nothing is running. */
  current: () => send<unknown | null>("/api/normalizations/current", undefined, "GET"),

  /** Cancel it. Idempotent: cancelling nothing is an answer, not an error. */
  cancel: () => send<{ cancelled: boolean }>("/api/normalizations/current", undefined, "DELETE"),
};

/**
 * Listen to a run.
 *
 * Returns the function that stops listening, so the caller can drop the stream when the window closes or
 * the component unmounts. `EventSource` reconnects on its own, which is why the engine replays its recent
 * history to a subscriber that arrives late: a window that was busy for a second catches up rather than
 * showing a gap.
 */
export function listen(onEvent: (event: EngineEvent) => void): () => void {
  let close: (() => void) | null = null;
  let stopped = false;
  void base().then((root) => {
    if (stopped) {
      return;
    }
    const source = new EventSource(`${root}/api/events`);
    source.onmessage = (message: MessageEvent<string>) => {
      try {
        onEvent(JSON.parse(message.data) as EngineEvent);
      } catch {
        // A frame that will not parse is not a reason to tear down the stream.
      }
    };
    close = () => source.close();
  });
  return () => {
    stopped = true;
    close?.();
  };
}

/* ==========================================================================================
   The window's own powers: dialogs and Explorer
   ========================================================================================== */

/**
 * The bridge the Electron preload installs.
 *
 * Every member is optional and the page is written to run without it, because the same code runs in a
 * browser during development where `electronAPI` is `undefined`. Nothing here is a filesystem or a
 * process handle: two dialogs, one Explorer window, one fullscreen toggle and the engine's address.
 */
export interface ElectronBridge {
  /**
   * Ask for media with the operating system's dialog. An empty list when cancelled.
   *
   * `startIn` is the folder the dialog opens in — the folder of the files already in the queue. It is a
   * **hint and not a restriction**: the operator browses wherever the file actually is, and a folder that
   * no longer exists costs a navigation rather than a refusal. `""` means "no folder to offer".
   */
  readonly openMedia?: (title: string, startIn?: string) => Promise<readonly string[]>;
  readonly saveMedia?: (suggested: string) => Promise<string | null>;
  readonly reveal?: (target: string) => Promise<{ readonly ok: boolean; readonly error?: string }>;
  readonly toggleFullscreen?: () => Promise<boolean>;
  readonly isFullscreen?: () => Promise<boolean>;
  readonly backendUrl?: () => Promise<string>;
}

declare global {
  interface Window {
    readonly electronAPI?: ElectronBridge;
  }
}

/**
 * The bridge the Electron preload installs, or an empty one.
 *
 * **`typeof window` rather than `window`.** This module is imported by the reducer's tests, which run in
 * Node where there is no `window` at all — and a bare `window.electronAPI` there is a `ReferenceError` at
 * import time, so the whole test file fails to load before a single test runs. The guard costs one
 * comparison and makes the module importable anywhere, which is the same property that lets the page run
 * in a plain browser during development.
 */
export const desktop: ElectronBridge =
  typeof window === "undefined" ? {} : (window.electronAPI ?? {});

/** Ask for media. An empty list when the operator cancels, which is a decision and not a fault. */
export async function openMedia(title: string, startIn = ""): Promise<readonly string[]> {
  return (await desktop.openMedia?.(title, startIn)) ?? [];
}

/** Where the result should go. `null` when cancelled. */
export async function saveOutput(suggested: string): Promise<string | null> {
  return (await desktop.saveMedia?.(suggested)) ?? null;
}

/**
 * Show a file in Explorer.
 *
 * This is the window's job and not the engine's: a file manager is a thing the operating system owns, and
 * a loopback HTTP route that shells out to `explorer.exe` on any path a request names is a route that did
 * not need to exist.
 */
export async function reveal(path: string): Promise<void> {
  if (desktop.reveal === undefined) {
    throw new ApiFailure("Showing a file in Explorer needs the desktop window; there is none.");
  }
  const answer = await desktop.reveal(path);
  if (!answer.ok) {
    throw new ApiFailure(answer.error ?? `${path} could not be shown`);
  }
}
