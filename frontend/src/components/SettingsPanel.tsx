/**
 * The settings: two controls, their units, and one line of help each.
 *
 * ## What this panel used to be, and why it is not that any more
 *
 * It was two zones, each with a header carrying a title *and* a note, each row carrying a label *and* a
 * suffix, and five paragraphs of prose running under them. Measured, that was **655 px** of column for two
 * fields — and it said the same thing three times: "LEVEL / what every file comes out at" over "Level …
 * dBFS peak" over a paragraph beginning "Every finished file comes out as loud as this". Three layers of
 * labelling around one text field, and the paragraphs were most of the height.
 *
 * The cost was not only reading. That 655 px is what forced the window to be nearly a thousand pixels tall,
 * which is what made it look like a window with the wrong amount of space in it.
 *
 * So: no zone headers, no repeated note under each field, and where something is genuinely not guessable —
 * what a dBFS value *does*, what a drive value *sounds* like — one line, italic, dimmed, under the field it
 * is about. Everything else is in `docs/` for the person who wants it and out of the way of the person who
 * does not.
 *
 * ## The words
 *
 * **Level** and **Drive**, not *Target*, *Make up* and *Limiter*. Those are the correct names and they are
 * the names an engineer uses, which is exactly the problem: a field whose label has to be looked up is a
 * field that gets left alone. One word each, and one line under it.
 */

import { useEffect, useState } from "react";

import type { Health } from "../api";
import { decibels, fieldValue } from "../lib/format";
import type { Settings } from "../state/queue";

interface Props {
  readonly settings: Settings;
  readonly onChangeApplied: (next: Settings) => void;
  readonly health: Health | null;
  readonly busy: boolean;
  /** Called when the operator clicks "Suggest" — App computes values from the measured file. */
  readonly onSuggest?: () => void;
  /** True when a file with measurements is selected and suggestions can be offered. */
  readonly canSuggest?: boolean;
}

/**
 * A labelled number field.
 *
 * The commit is on every keystroke that parses, which is what makes the plan beside it move as the operator
 * types. A value that does not parse stays in the field and changes nothing.
 *
 * `id` is not decoration: the label is a `<label htmlFor>`, so clicking it focuses the field — and a control
 * whose visible label says "Level" while the field announces nothing is a control a screen reader cannot
 * name.
 */
function NumberField({
  id,
  label,
  value,
  unit,
  onCommit,
  disabled,
}: {
  readonly id: string;
  readonly label: string;
  readonly value: number;
  readonly unit: string;
  readonly onCommit: (next: number) => void;
  readonly disabled?: boolean;
}) {
  const [text, setText] = useState(() => fieldValue(value));

  // A value that changed from outside is shown. One that changed because of this field's own commit is
  // already what the field says, so nothing moves under the cursor mid-keystroke.
  useEffect(() => {
    setText((current) => (decibels(current) === value ? current : fieldValue(value)));
  }, [value]);

  return (
    <div className="setting">
      <label className="setting__label" htmlFor={id}>
        {label}
      </label>
      <div className="setting__control">
        <input
          id={id}
          className="number-field"
          type="text"
          inputMode="decimal"
          value={text}
          disabled={disabled === true}
          aria-label={label}
          onChange={(event) => {
            setText(event.target.value);
            const parsed = decibels(event.target.value);
            if (parsed !== null) {
              onCommit(parsed);
            }
          }}
          onBlur={() => {
            const parsed = decibels(text);
            setText(parsed === null ? fieldValue(value) : fieldValue(parsed));
          }}
        />
        {/*
          The unit is inside the control and attached to the value it belongs to. It used to be a separate
          column of the row, which put "dBFS peak" and "dB of push" the same distance from every field
          whether or not it was that field's unit.
        */}
        <span className="setting__unit">{unit}</span>
      </div>
    </div>
  );
}

export function SettingsPanel({ settings, onChangeApplied, health, busy, onSuggest, canSuggest }: Props) {
  const set = (patch: Partial<Settings>) => {
    onChangeApplied({ ...settings, ...patch });
  };

  const mp3Available = health === null || health.libmp3lame;

  return (
    <section className="zone" aria-label="Settings">
      <div className="zone__head">
        <h2 className="zone__title">Settings</h2>
        <span className="zone__note">Target &amp; Dynamics</span>
        <span className="spacer" />
        {onSuggest ? (
          <button
            type="button"
            className="btn btn--ghost btn--small"
            onClick={onSuggest}
            disabled={!canSuggest || busy}
            title={
              busy
                ? "Cannot change settings while a run is in progress"
                : canSuggest
                  ? "Suggest settings from this file's measurements"
                  : "Select a measured file to get suggestions"
            }
          >
            Suggest
          </button>
        ) : null}
      </div>
      <div className="zone__body">
        <NumberField
          id="level"
          label="Level"
          unit="dBFS — the limiter's ceiling"
          value={settings.levelDbfs}
          onCommit={(next) => set({ levelDbfs: next })}
          disabled={busy}
        />
        {/*
          One line, and only because it is genuinely not guessable: "level" says what the field *is* and
          nothing about which way to move it. The numbers that used to be here — −3 and −1 for a hotter
          delivery — are in the note's own sentence now rather than in a list of three.
        */}
        <p className="setting__hint">
          Every file comes out at this level. <span className="figures">−6</span> sits beside a stitched
          episode; go to <span className="figures">−1</span> to deliver hotter.
        </p>

        <NumberField
          id="leveling"
          label="Even out"
          unit="of leveling"
          value={settings.levelingDb}
          onCommit={(next) => set({ levelingDb: next })}
          disabled={busy}
        />
        {/*
          This control is the one that answers the report that a normalized interview still had quiet parts,
          and it is why it has a line of its own rather than sharing Drive's. `Level` decides how loud the
          file is; `Even out` decides how far apart its own quiet and loud parts are; `Drive` decides how
          hard the peaks are pushed down to reach the level. Only this one changes what a person means by
          "the guest sounds quiet".
        */}
        <p className="setting__hint">
          Lifts the quiet passages toward the loud ones so a file sounds even all the way through.{" "}
          <span className="figures">12</span> is where this was tuned. Still quiet? Raise it. Flat or
          squashed? Lower it.
        </p>

        <div className="setting setting--toggles">
          <span className="setting__label">Also write</span>
          <div className="setting__control">
            <label className="setting__toggle">
              <input
                type="checkbox"
                checked={settings.audio.includes(".wav")}
                disabled={busy}
                onChange={(event) =>
                  set({
                    audio: event.target.checked
                      ? [".wav", ...settings.audio.filter((format) => format !== ".wav")]
                      : settings.audio.filter((format) => format !== ".wav"),
                  })
                }
              />
              WAV
            </label>
            <label className="setting__toggle">
              <input
                type="checkbox"
                checked={settings.audio.includes(".mp3")}
                disabled={busy || !mp3Available}
                onChange={(event) =>
                  // Asking for the MP3 asks for the WAV with it: it is encoded *from* the WAV the same run
                  // writes, so the two are one request and the engine refuses the pair split.
                  set({
                    audio: event.target.checked
                      ? [".wav", ".mp3"]
                      : settings.audio.filter((format) => format !== ".mp3"),
                  })
                }
              />
              MP3
            </label>
          </div>
        </div>
        {/*
          The note is written from what this machine reported. It is here rather than dispatching on
          `health` because a disabled control with no explanation is the worst of the three states.
        */}
        <p className="setting__hint">
          {mp3Available
            ? "The finished sound as separate files beside the video."
            : health === null
              ? "Asking this machine what it can write…"
              : "No MP3 encoder here, so only the WAV can be written."}
        </p>

        <NumberField
          id="drive"
          label="Make up"
          unit="dB into the limiter"
          value={settings.driveDb}
          onCommit={(next) => set({ driveDb: next })}
          disabled={busy}
        />
        {/*
          **This control is the operator's own make-up figure**, and it was called "Drive" for two revisions,
          which was a name nobody could map onto anything. It is a `volume` fader in front of the compressor
          — the same place the Premiere Track Fx's make-up gain sits — and what it decides is how hard the
          material is pushed into the limiter at the end of the chain, which is what decides how squashed the
          peaks come out. It does not change the file's level: `Level` is what the file peaks at, and the run
          corrects to it whatever this is set to.
        */}
        <p className="setting__hint">
          The same make-up gain as the Track Fx: how hard the sound is pushed into the limiter, so a higher
          value squashes the peaks down harder. <span className="figures">+12</span> is the edit bay's
          setting. It does not change how loud the file is — <span className="figures">Level</span> does that.
        </p>
      </div>

      {/*
        What the three controls make, in one line and in the filters' own words.
        
        This exists because the three settings are named in three different vocabularies — a target level in
        dBFS, a make-up figure in dB and a leveler in "expansion" — and an operator who set up the Premiere
        Track Fx knows two of them by their Premiere names and has never met the third. A single sentence
        saying *what runs, in order* is shorter than any explanation of it, and it is generated from the
        settings rather than written out, so it cannot drift from what a run will do.
      */}
      <p className="recipe" aria-live="polite">
        <span className="recipe__step">make up {settings.driveDb.toFixed(1)} dB</span>
        <span className="recipe__arrow" aria-hidden="true">
          →
        </span>
        <span className="recipe__step">
          even out {settings.levelingDb <= 0 ? "off" : settings.levelingDb.toFixed(0)}
        </span>
        <span className="recipe__arrow" aria-hidden="true">
          →
        </span>
        <span className="recipe__step">limiter {settings.levelDbfs.toFixed(1)} dBFS</span>
      </p>
    </section>
  );
}
