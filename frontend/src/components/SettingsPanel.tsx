/**
 * The settings: the level every file is taken to, how the sound gets there, and what else is written.
 *
 * ## Four controls, and why only four
 *
 * An operator normalizing an episode's worth of files is making one decision — *what level* — and every
 * other control here exists because that one decision has a shape. The target is the level; the strategy
 * is whether the sound is compressed on the way to it; the drive and the ceiling are the two figures the
 * operator's own Premiere timeline uses, for the one strategy that reproduces it; the sound files are
 * extras.
 *
 * Everything else the engine accepts — the bitrate, the preset, the trim — is either not a decision an
 * operator wants to make per episode or is a second place for the run to be wrong, so it is not here.
 *
 * ## Why the drive and the ceiling are disabled outside the chain
 *
 * They are the chain's, and on a `gain` or `ceiling` run there is no chain: a live control that changed
 * nothing would be a control that lied about what it does. Disabled rather than hidden, because the value
 * is still the operator's and switching strategy back finds it where they left it.
 *
 * ## Why a field keeps its own text
 *
 * A number field is a string until it is parsed. If it rendered `settings.targetDbfs` directly, typing
 * `-` would parse as `null`, the setting would not change, and the field would snap back to the old value
 * under the cursor — mid-keystroke. So each field holds what was typed and the *number* only moves when
 * the text parses. See `decibels` in `lib/format.ts`.
 */

import { useEffect, useState } from "react";

import type { Health } from "../api";
import { bytes, fieldValue } from "../lib/format";
import type { Settings } from "../state/queue";
import { decibels } from "../lib/format";

interface Props {
  readonly settings: Settings;
  readonly onChange: (next: Settings) => void;
  /** Called when a change should re-plan the selected file. Same value; the name says what it is for. */
  readonly onChangeApplied: (next: Settings) => void;
  readonly health: Health | null;
  readonly busy: boolean;
  readonly output: string | null;
  readonly onPickOutput: () => void;
  readonly onClearOutput: () => void;
}

/**
 * A number field that holds what was typed.
 *
 * The commit is on every keystroke that parses, which is what makes the plan beside it move as the
 * operator types — the product's whole claim is that the numbers are stated before the button is pressed.
 * A value that does not parse is left in the field and changes nothing.
 *
 * `id` is not decoration: the label beside it is a `<label htmlFor>`, so clicking the label focuses the
 * field, and a control whose label says "Target" while the field announces nothing is a control that a
 * screen reader cannot name. The first version of this drew the label and never connected the two.
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

  // A value that changed from outside — a strategy switch, a default restored — is shown. One that
  // changed because of this field's own commit is already what the field says, so nothing moves.
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

export function SettingsPanel({
  settings,
  onChange,
  onChangeApplied,
  health,
  busy,
  output,
  onPickOutput,
  onClearOutput,
}: Props) {
  const chain = settings.strategy === "chain";

  /** One place where a change is made, so the re-plan is never forgotten. */
  const set = (patch: Partial<Settings>) => {
    onChangeApplied({ ...settings, ...patch });
  };

  const mp3Available = health === null || health.libmp3lame;

  return (
    <>
      <section className="zone" aria-label="Level">
        <div className="zone__head">
          <h2 className="zone__title">Level</h2>
          <span className="zone__note">every file is taken to this peak</span>
        </div>
        <div className="zone__body">
          <div className="field-row">
            <label className="field-row__label" htmlFor="target">
              Target
            </label>
            <span className="field-row__value">
              <NumberField
                id="target"
                label="Target peak level in dBFS"
                value={settings.targetDbfs}
                onCommit={(next) => set({ targetDbfs: next })}
                suffix="dBFS"
              />
            </span>
            <span />
          </div>
          <p className="field-row__prose">
            What every finished file will peak at. −6 dBFS is what a stitched episode sits at, so a file
            normalized here sits beside one.
          </p>

          <div className="field-row">
            <label className="field-row__label" htmlFor="strategy">
              Sound
            </label>
            <span className="field-row__value">
              <span className="select">
                <select
                  id="strategy"
                  value={settings.strategy}
                  disabled={busy}
                  onChange={(event) =>
                    set({ strategy: event.target.value as Settings["strategy"] })
                  }
                >
                  <option value="gain">One gain — nothing compressed</option>
                  <option value="chain">The operator's chain — compressor and limiter</option>
                  <option value="ceiling">Gain, then a limiter at the target</option>
                </select>
              </span>
            </span>
            <span />
          </div>
          <p
            className={
              settings.strategy === "chain" && settings.targetDbfs > settings.ceilingDbfs - 3
                ? "field-row__prose field-row__prose--warn"
                : "field-row__prose"
            }
          >
            {settings.strategy === "gain"
              ? "One gain and nothing else: the sound is lifted or lowered to the target and its dynamics are untouched. This is the one that reaches any target."
              : settings.strategy === "chain"
                ? `The operator's Premiere Track Fx: ${settings.makeupDb.toFixed(1)} dB of drive into a ${settings.ceilingDbfs.toFixed(1)} dBFS limiter. Sounds like a stitched episode, and cannot peak above the limiter's own level.`
                : "The gain to the target, then a limiter at the target. For lifting quiet material hard: the limiter is what stops the lift clipping."}
          </p>
        </div>
      </section>

      <section className="zone" aria-label="The chain">
        <div className="zone__head">
          <h2 className="zone__title">The chain</h2>
          <span className="zone__note">
            {chain ? "the operator's two figures" : "not used by this strategy"}
          </span>
        </div>
        <div className="zone__body">
          <div className="field-row">
            <label className="field-row__label" htmlFor="makeup">
              Make up
            </label>
            <span className="field-row__value">
              <NumberField
                id="makeup"
                label="Make-up gain in decibels"
                value={settings.makeupDb}
                onCommit={(next) => set({ makeupDb: next })}
                suffix="dB of drive"
                disabled={!chain}
              />
            </span>
            <span />
          </div>
          <p className="field-row__prose">
            How hard the material is driven into the limiter. +12 dB is TheStitcher's own setting.
          </p>

          <div className="field-row">
            <label className="field-row__label" htmlFor="ceiling">
              Limiter
            </label>
            <span className="field-row__value">
              <NumberField
                id="ceiling"
                label="Limiter ceiling in dBFS"
                value={settings.ceilingDbfs}
                onCommit={(next) => set({ ceilingDbfs: next })}
                suffix="dBFS ceiling"
                disabled={!chain}
              />
            </span>
            <span />
          </div>
          <p className="field-row__prose">
            The level the chain will not let a sample past. −6 dBFS is the operator's own setting.
          </p>
        </div>
      </section>

      <section className="zone" aria-label="Output">
        <div className="zone__head">
          <h2 className="zone__title">Output</h2>
          <span className="zone__note">
            {output === null ? "beside each source" : "one file's destination"}
          </span>
        </div>
        <div className="zone__body">
          <div className="field-row">
            <span className="field-row__label">Where</span>
            <span className="field-row__value">
              <input
                className="path-field"
                type="text"
                readOnly
                value={output ?? ""}
                placeholder={`next to each source, as "<name> - normalized.<ext>"`}
                aria-label="Destination for a single file"
                disabled={busy}
              />
              <button
                type="button"
                className="btn"
                onClick={onPickOutput}
                disabled={busy || health === null}
              >
                Choose…
              </button>
              {output === null ? null : (
                <button type="button" className="btn btn--ghost" onClick={onClearOutput} disabled={busy}>
                  Beside source
                </button>
              )}
            </span>
            <span />
          </div>
          <p className="field-row__prose">
            A batch writes each normalized file beside its own source; naming one path is for a single
            file. Nothing is ever written over: a name that is taken steps aside.
          </p>
        </div>
      </section>

      <section className="zone" aria-label="Sound files">
        <div className="zone__head">
          <h2 className="zone__title">Sound files</h2>
          <span className="zone__note">written beside each master</span>
        </div>
        <div className="zone__body">
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
                        ? (["wav", "mp3"] as const).map((kind) =>
                            kind === "wav" ? ".wav" : ".mp3",
                          )
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
              ? "The finished sound, as uncompressed 16-bit WAV and as a 320 kbps MP3 encoded from that WAV. Off by default: these are extras, and a run that wrote files nobody asked for would leave litter."
              : health === null
                ? "Asking this machine what it can write…"
                : "This machine's ffmpeg has no MP3 encoder, so only the WAV can be written."}
          </p>
        </div>
      </section>
    </>
  );
}
