/**
 * The settings: what level the files come out at, and how hard the sound is driven to get there.
 *
 * ## Two controls, and why only two
 *
 * An operator normalizing an episode's worth of files is making one decision — *what level* — and this
 * window used to offer five fields for it. A field that is not a decision is worse than no field: it is a
 * question the operator has to answer or ignore, and either way it costs them the time it takes to work
 * out that it does not matter.
 *
 * So there are two, and each one says what it does in plain words:
 *
 * * **Level** — the peak every finished file will have. This is the decision.
 * * **Drive** — how hard the sound is pushed into the limiter. This is the one that changes *how* it
 *   sounds rather than how loud it is, and its note explains that in a sentence rather than in the
 *   vocabulary of a mixing desk.
 *
 * Everything else the engine can do — the strategy, the trim, a named destination — is either fixed at
 * the value this product is for or has been taken out of the window entirely. `Settings` in
 * `state/queue.ts` records which is which and why.
 *
 * ## Why the words are the words
 *
 * The first version of this panel said *Target*, *Sound*, *Make up* and *Limiter*. Those are the correct
 * names and they are the names an engineer uses, which is exactly the problem: "make up" and "limiter" are
 * two words that mean nothing to somebody who has not sat in front of a mixing desk, and a field whose
 * label has to be looked up is a field that gets left alone. So each one says what it does — **Level** and
 * **Drive** — and carries a sentence underneath saying what changing it will sound like.
 *
 * The engine still calls them `target_dbfs`, `ceiling_dbfs` and `makeup_db`, because those are the correct
 * names and the code is read by people who know them. Only the window talks like a person.
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
}

/**
 * A number field that holds what was typed.
 *
 * The commit is on every keystroke that parses, which is what makes the report beside it move as the
 * operator types — the product's claim is that the numbers are stated before the button is pressed. A
 * value that does not parse is left in the field and changes nothing.
 *
 * `id` is not decoration: the label beside it is a `<label htmlFor>`, so clicking the label focuses the
 * field, and a control whose label says "Level" while the field announces nothing is a control a screen
 * reader cannot name.
 */
function NumberField({
  id,
  label,
  value,
  onCommit,
  suffix,
  disabled,
}: {
  readonly id: string;
  readonly label: string;
  readonly value: number;
  readonly onCommit: (next: number) => void;
  readonly suffix: string;
  readonly disabled?: boolean;
}) {
  const [text, setText] = useState(() => fieldValue(value));

  // A value that changed from outside is shown. One that changed because of this field's own commit is
  // already what the field says, so nothing moves under the cursor mid-keystroke.
  useEffect(() => {
    setText((current) => (decibels(current) === value ? current : fieldValue(value)));
  }, [value]);

  return (
    <>
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
      <span className="field-row__note">{suffix}</span>
    </>
  );
}

export function SettingsPanel({ settings, onChangeApplied, health, busy }: Props) {
  const set = (patch: Partial<Settings>) => {
    onChangeApplied({ ...settings, ...patch });
  };

  const mp3Available = health === null || health.libmp3lame;

  return (
    <>
      <section className="zone" aria-label="Level">
        <div className="zone__head">
          <h2 className="zone__title">Level</h2>
          <span className="zone__note">what every file comes out at</span>
        </div>
        <div className="zone__body">
          <div className="field-row">
            <label className="field-row__label" htmlFor="level">
              Level
            </label>
            <span className="field-row__value">
              <NumberField
                id="level"
                label="The peak level every finished file will have, in dBFS"
                value={settings.levelDbfs}
                onCommit={(next) => set({ levelDbfs: next })}
                suffix="dBFS peak"
                disabled={busy}
              />
            </span>
            <span />
          </div>
          <p className="field-row__prose">
            Every finished file comes out as loud as this, whatever it arrived as: quieter files are lifted
            and louder ones are brought down. <span className="figures">−6</span> is where a stitched
            episode sits, so a file normalized here sits beside one.{" "}
            <span className="figures">−3</span> or <span className="figures">−1</span> is for delivering to
            somebody who wants it hotter.
          </p>

          <div className="field-row">
            <span className="field-row__label">Also write</span>
            <span className="field-row__value field-row__value--toggles">
              <label className="field-row__toggle">
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
              <label className="field-row__toggle">
                <input
                  type="checkbox"
                  checked={settings.audio.includes(".mp3")}
                  disabled={busy || !mp3Available}
                  onChange={(event) =>
                    // Asking for the MP3 asks for the WAV with it: it is encoded *from* the WAV the same
                    // run writes, so the two are one request and the engine refuses the pair split.
                    set({
                      audio: event.target.checked
                        ? [".wav", ".mp3"]
                        : settings.audio.filter((format) => format !== ".mp3"),
                    })
                  }
                />
                MP3
              </label>
            </span>
            <span />
          </div>
          <p className="field-row__prose">
            {mp3Available
              ? "The finished sound as separate files beside the video — a plain WAV and a 320 kbps MP3. Off by default: these are extras, and a run that wrote files nobody asked for would leave litter."
              : health === null
                ? "Asking this machine what it can write…"
                : "This machine's ffmpeg has no MP3 encoder, so only the WAV can be written."}
          </p>
        </div>
      </section>

      <section className="zone" aria-label="Drive">
        <div className="zone__head">
          <h2 className="zone__title">Drive</h2>
          <span className="zone__note">how hard the sound is pushed</span>
        </div>
        <div className="zone__body">
          <div className="field-row">
            <label className="field-row__label" htmlFor="drive">
              Drive
            </label>
            <span className="field-row__value">
              <NumberField
                id="drive"
                label="How hard the sound is driven into the limiter, in decibels"
                value={settings.driveDb}
                onCommit={(next) => set({ driveDb: next })}
                suffix="dB of push"
                disabled={busy}
              />
            </span>
            <span />
          </div>
          <p className="field-row__prose">
            The only control here that changes how a file <em>sounds</em> rather than how loud it is. It
            lifts the quiet parts and holds the loudest ones down at the level above, so a higher value
            makes a file sound more even and less dynamic.{" "}
            <span className="figures">+12</span> is the setting this family's edit bay uses;{" "}
            <span className="figures">0</span> means the sound is only moved to the level and not touched
            otherwise.
          </p>
          <p className="field-row__prose">
            The level is reached whatever this is set to — this decides the character, not the loudness. If
            a file comes out sounding flat or squashed, lower it.
          </p>
        </div>
      </section>
    </>
  );
}
