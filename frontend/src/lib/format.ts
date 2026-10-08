/**
 * The window's own little library: a level as a sentence, a byte count as a magnitude, a number as the
 * thing a field means.
 *
 * ## Why this is a module of its own
 *
 * Everything here is arithmetic on a **string or a number the engine already measured** — never on a
 * measurement itself. The distinction is the product's R20: no figure the engine could have measured is
 * computed in the interface, so what lives here is presentation and parsing, and nothing that could
 * disagree with the engine about what a file is.
 *
 * ## The four conversions, and why each is here rather than inline
 *
 * * `clock` — seconds as `H:MM:SS.mmm`. The engine also sends its own formatted form, and where it does
 *   the engine's is used; this exists for the elapsed time of a run, which is the *window's* clock and
 *   not a measurement of anything.
 * * `bytes` — a byte count as a magnitude. Decimal and not binary, because a WAV's size is quoted in
 *   decimal by every tool that will open it.
 * * `decibels` — a text field as the number of decibels it means, or `null`. A field is a string until
 *   it is parsed, and a field that says `-6.0` and a field that says `` are different requests.
 * * `duration` — the engine's `H:MM:SS.mmm` back as a number, for a label that wants seconds.
 */

/** Seconds as `H:MM:SS.mmm`. Nine hours of runtime and no frame field: sound has no frame grid. */
export function clock(seconds: number): string {
  const whole = Math.max(0, seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const rest = whole - hours * 3600 - minutes * 60;
  return `${hours}:${String(minutes).padStart(2, "0")}:${rest.toFixed(3).padStart(6, "0")}`;
}

/**
 * A byte count as a magnitude, in decimal units.
 *
 * **Decimal**, because the figure is a *file size* and a file size is counted in decimal by every tool
 * that will ever open the file — a 190 MB WAV is 190,000,000 bytes, not 199,229,440. This is also what
 * `TheVideoTranscript`'s `human_size` does, so the family agrees.
 */
export function bytes(count: number): string {
  if (!Number.isFinite(count) || count <= 0) {
    return "0 B";
  }
  const units = ["B", "kB", "MB", "GB", "TB"];
  let value = count;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  // A unit's own magnitude decides its precision: `190 MB` and `1.5 GB` are the same promise, and
  // `190.0 MB` is a figure pretending to a precision a size estimate does not have.
  const digits = value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(digits)} ${units[unit] ?? "B"}`;
}

/**
 * A text field as the number of decibels it means, or `null` when it is not a number.
 *
 * `null` rather than `NaN` or `0`: a field the operator has emptied and a field that holds `0.0` are
 * different requests, and `parseFloat("")` is `NaN` while `Number("")` is `0` — the second of which would
 * silently ask for zero decibels of make-up gain.
 */
export function decibels(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === "") {
    return null;
  }
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

/** A number as the field shows it: one decimal, and never an exponent. */
export function fieldValue(value: number): string {
  return value.toFixed(1);
}

/**
 * A level as the sentence a row shows.
 *
 * `null` is digital silence and says so. A very negative number would be arithmetic waiting to happen on
 * a quantity that is not there.
 */
export function level(peakDbfs: number | null): string {
  return peakDbfs === null ? "silence" : `${peakDbfs.toFixed(1)} dBFS`;
}

/** How far a measured level sits from a target, as `+0.30` / `-0.30`. */
export function signed(value: number, places = 2): string {
  const rounded = value.toFixed(places);
  return value >= 0 ? `+${rounded}` : rounded;
}

/** The folder a path is in, for a dialog's starting point. Both separators, and never a trailing one. */
export function folderOf(path: string): string {
  const cut = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
  return cut <= 0 ? "" : path.slice(0, cut);
}

/** A path's file name, whichever separator it uses. */
export function nameOf(path: string): string {
  const cut = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
  return cut < 0 ? path : path.slice(cut + 1);
}

/**
 * A path's name without its extension. Used for a label, never for a file name — the engine decides what
 * the output is called, and a second spelling of that here is a second answer that can disagree.
 */
export function stemOf(path: string): string {
  const name = nameOf(path);
  const dot = name.lastIndexOf(".");
  return dot <= 0 ? name : name.slice(0, dot);
}
