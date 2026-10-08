"""The normalization: what was asked for, what is possible, and the commands that carry it out.

## The shape of a run

With ``P`` the source's measured peak in dBFS and ``T`` the target:

```
gain   = T - P                       the one number the request decides
chain  = +12 dB into a −6 dBFS limiter   the operator's Premiere Track Fx, reproduced
output = (source sound × chain) × gain
video  = the source's picture, stream-copied
```

The result is a file whose peak sits at ``T`` — measured, not hoped for — with the sound of a stitched
episode and a picture that has not been re-encoded. That second half is the whole reason the picture is
copied rather than filtered: a normalizer that re-encoded the picture would cost minutes and a
generation of quality to change a number that lives entirely in the sound.

## Why the gain is after the chain, and why that is not the obvious order

The obvious order is to apply the gain to the source and let the chain reach the target by itself: if
the chain is a flat +12 dB into a −6 dBFS limiter, then anything at −18 dBFS or louder comes out at
−6 dBFS and the normalizing appears to be free. That is what the sibling product does — and it is why
its episodes sit at −6 dBFS, a number nobody chose.

It is not what this product does, and the difference is the *verifiable* one. A limiter's output level
depends on the program material: a signal that only touches the limiter for one sample comes out at the
ceiling, while a signal that sits on it comes out at the ceiling too but with its dynamics flattened.
The peak that comes out is therefore a statement about the loudest sample, and the target the operator
asked for is reached only if the material got there. Applying the requested gain *after* the chain
means the last thing done to the signal is the thing the request names, so the peak is ``T`` because of
the request rather than because of the material — and the plan can state, before the run, what the
output's level will be.

## The three strategies, and what each one keeps

| Strategy | The sound | The picture |
|---|---|---|
| ``chain`` (default) | the operator's dynamics chain, then the gain to the target | stream copy |
| ``gain`` | the gain to the target, and nothing else | stream copy |
| ``ceiling`` | the gain to the target, then a limiter at the target | stream copy |

``gain`` is the honest one for material that is already mixed: it changes one number and touches
nothing else. ``ceiling`` is what a **loudness** request wants — bring quiet material up, and catch
what would clip — and it is the only one of the three that can *increase* the level of a hot source
without any material-dependent behaviour. ``chain`` is the default because it is what the sibling
product's operator had on their Premiere timeline, so a file normalized here sits beside a stitched
episode rather than beside a different-sounding one.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path

from .media import MediaInfo
from .process import CancelToken, Cancelled, FFmpegError, capture, measure_levels, run, tool

#: The operator's dynamics chain, built from the two figures they set on their Premiere timeline.
#:
#: ## What is reproduced, and what is exposed
#:
#: | Premiere Track Fx | ffmpeg | Exposed? |
#: |---|---|---|
#: | Compressor threshold −20 dB | `acompressor threshold=-20dB` | no — it does nothing at ratio 1 |
#: | Compressor ratio 1 | `ratio=1` | no — that *is* the "flat fader" the operator set |
#: | Compressor attack 1 ms | `attack=1` | no |
#: | Compressor release 50 ms | `release=50` | no |
#: | **Compressor make up 12 dB** | `makeup=3.9812` | **yes — `makeup_db`** |
#: | **Limiter −6 dBFS** | `alimiter limit=0.5012` | **yes — `ceiling_dbfs`** |
#: | Limiter release 50 ms | `release=50` | no |
#: | *(no attack shown in Premiere)* | `alimiter`'s own default | no |
#:
#: The two that are exposed are the two that decide what the chain *does*: how hard the material is
#: driven, and how much of it is allowed through. The rest are the shape of the compressor the operator
#: chose, and a control for each of them would be a compressor design tool rather than this product.
#:
#: ## `makeup` is a MULTIPLIER, not decibels
#:
#: `makeup=3.9812` is +12 dB, and `3.9812` is `10 ** (12 / 20)`. The option's range is 1..64, which is
#: 0..+36 dB, and a caller who writes `makeup=12` asking for twelve decibels gets **+21.6 dB** and a
#: file that clips. So the field is named in decibels and converted here, once, by
#: :func:`makeup_multiplier`.
#:
#: ## `level=0` is not optional
#:
#: `alimiter` defaults to `level=1`, automatic level, which adds about **+3 dB** on top of its ceiling
#: — measured on this machine: a sine driven +30 dB through `limit=0.5012` comes out at −3.0 dBFS with
#: `level=1` and at −9.0 dBFS with `level=0`. Left at the default, every file here would be hotter than
#: the same material exported from Premiere, by an amount that depends on the material.
#:
#: ## The chain is the sibling's, and the two figures are the sibling's
#:
#: `TheStitcher` applies exactly this chain, with these two values, to every episode it stitches. A file
#: normalized here has to sit beside one of those without sounding wrong, which is why the defaults are
#: what they are rather than something this product preferred.
DEFAULT_MAKEUP_DB = 12.0
DEFAULT_CEILING_DBFS = -6.0

#: The strategy every request gets unless it names another. `gain` and not `chain`, because a limiter is
#: a ceiling and a strategy containing one cannot reach an arbitrary target — see `STRATEGIES`. Named
#: here so the engine, the route and the window have one spelling of "the default".
DEFAULT_STRATEGY = "gain"

#: The ranges the two chain figures may be asked for in.
#:
#: `makeup` is bounded at +36 dB because that is the ffmpeg option's own ceiling (`makeup=64`), and at
#: −12 dB because a *negative* make-up is an attenuator, which is what the target field is for. The
#: ceiling is bounded at 0 dBFS because a ceiling above full scale is not a ceiling, and at −24 because
#: below that the chain is squashing everything to a whisper for no reason a person would have.
MAKEUP_RANGE = (-12.0, 36.0)
CEILING_RANGE = (-24.0, 0.0)


def makeup_multiplier(db: float) -> float:
    """Decibels as `acompressor`'s `makeup` option wants them: a linear multiplier.

    ``makeup=3.9812`` is +12 dB, and `3.9812` is `10 ** (12 / 20)`. Every place in this file that names
    a make-up gain goes through this one function, so a control and a command can never disagree about
    what a decibel is.
    """
    return round(10.0 ** (db / 20.0), 6)


def _linear(dbfs: float) -> float:
    """dBFS as the linear amplitude `alimiter`'s `limit` option wants.

    ``limit=0.5012`` is the −6 dBFS ceiling, and `0.5012` is `10 ** (-6 / 20)` to four places. The same
    conversion as :func:`makeup_multiplier`, named separately because the two options mean different
    things by it: `makeup` scales the signal, `limit` is the level it is not allowed past.
    """
    return round(10.0 ** (dbfs / 20.0), 6)


#: The operator's chain: a compressor that performs no gain reduction, and the limiter that does the
#: work. The drive into it is *not* here — see `NormalizeSpec.makeup_db`, which is the fader in front of
#: the whole chain so that it can attenuate as well as lift.
#:
#: | Premiere Track Fx | ffmpeg |
#: |---|---|
#: | Compressor threshold −20 dB | `acompressor threshold=-20dB` |
#: | Compressor ratio 1 | `ratio=1` — no gain reduction at all; the block is a fader |
#: | Compressor attack 1 ms | `attack=1` |
#: | Compressor release 50 ms | `release=50` |
#: | Compressor make up | left at unity: the drive is the fader in front of the chain |
#: | Limiter ceiling | `alimiter limit=<the request's>` |
#: | Limiter release 50 ms | `release=50` |
#: | *(no attack shown)* | `alimiter`'s own default |
#:
#: **`level=0` is not optional.** `alimiter` defaults to `level=1`, automatic level, which adds about
#: **+3 dB** on top of its ceiling — measured: a sine driven +30 dB through `limit=0.5012` comes out at
#: −3.0 dBFS with `level=1` and at −9.0 dBFS with `level=0`. Left at the default, every file here would
#: be hotter than the same material exported from Premiere.
CHAIN_COMPRESSOR = "acompressor=threshold=-20dB:ratio=1:attack=1:release=50:makeup=1"

#: How far under its nominal ceiling `alimiter` clamps material that is driven into it hard.
#:
#: Measured, and used for one thing only: the note the plan writes when the ceiling is set below the
#: target, so the operator is told the highest peak the chain can reach rather than finding out from the
#: file. It is **not** used to compute a gain — the run measures what the chain did instead, which is why
#: this figure being approximate costs nothing.
LIMITER_OFFSET_DB = 3.0


def dynamics_chain(ceiling_dbfs: float) -> str:
    """The operator's chain, with the limiter at the level asked for.

    The drive into it is the fader in front of the chain and is not a parameter here: see
    `NormalizeSpec.makeup_db`. What is left is the shape of their Track Fx — a compressor that does no
    gain reduction, and a limiter that does all the work.

    ## `level=0`, again

    Because `alimiter`'s automatic level would add about +3 dB on top of the ceiling, and the plan's
    arithmetic — and the note it writes about the highest peak the chain can reach — would then be
    describing a filter that is not the one running.
    """
    return (
        f"{CHAIN_COMPRESSOR},alimiter=limit={_linear(ceiling_dbfs)}:release=50:level=0"
    )


def chain_graph(ceiling_dbfs: float, front_gain_db: float, out_gain_db: float = 0.0) -> str:
    """The operator's chain with a fader on each side of it.

    **The front fader is the drive**, and it is where the operator's own `make up` field is: in front of
    the limiter, so it is the only thing that can change how hard the material hits it.

    **The out fader is what puts the result on the target**, and it is not an optimisation. A limiter
    clamps material into it to some level of its own that is *below* the ceiling it names and *depends on
    the drive*, so the chain's output is a measurement rather than an arithmetic: measured on a
    −2.0 dBFS source through +12 dB of make-up, a −6.0 dBFS ceiling delivered −5.10, a −3.0 ceiling
    delivered −8.10, and a −1.0 ceiling delivered −10.10. There is no formula for that, so the run
    measures it — in the stage pass, like everything else about the chain — and the out fader is what the
    measurement bought.

    The out fader is *after* the limiter, which is the one place this product puts a gain there. It is
    safe because the limiter has already bounded what reaches it: the fader's input cannot exceed the
    limiter's own output, so the fader cannot clip. Before this existed, the run had no way to deliver a
    target the limiter did not happen to land on.
    """
    return (
        f"volume={front_gain_db:.6f}dB,{dynamics_chain(ceiling_dbfs)}"
        f",volume={out_gain_db:.6f}dB"
    )


#: What the chain is, in the window's words. The two figures are filled in from the request, because
#: the window has to be able to say what it is about to do to the sound.
def dynamics_note(makeup_db: float, ceiling_dbfs: float) -> str:
    return (
        f"{makeup_db:+.1f} dB of make-up gain into a {ceiling_dbfs:.1f} dBFS limiter (the compressor at "
        f"ratio 1 performs no gain reduction — it is a flat fader), which is the operator's Premiere "
        f"Track Fx and the same chain TheStitcher applies."
    )


#: What the two chain figures do, for the window to explain them once rather than in a tooltip each.
CHAIN_HELP = {
    "makeup": (
        "How hard the material is driven into the limiter. +12 dB is the operator's Premiere setting "
        "and TheStitcher's. Raising it squashes more of the loud parts; the output level is set by the "
        "target, not by this."
    ),
    "ceiling": (
        "The level the chain will not let a sample past. −6 dBFS is the operator's Premiere setting. "
        "Lower is a firmer grip on peaks and a flatter result; the output's own level is the target, "
        "so a ceiling below the target only ever costs dynamics."
    ),
}

#: Which normalizing strategies exist, and what each one is for.
#:
#: ## Why `gain` is the default and `chain` is not
#:
#: A limiter is a **ceiling**: everything driven into it comes out at about one level, so a chain
#: containing one cannot deliver an arbitrary target — it delivers whatever its ceiling allows, and the
#: measured figure is `ceiling_dbfs − 3 dB`. That makes `chain` the right tool for exactly the job it
#: was built for (material at the operator's own operating level, sounding like a stitched episode) and
#: the wrong default for a normalizer, whose whole promise is "this file will peak at the level you
#: asked for".
#:
#: `gain` has no limiter, so that promise holds for every source at every target. It is the default for
#: that reason, and `chain` is one selection away for material that should match a stitched episode.
STRATEGIES: dict[str, str] = {
    "gain": "one gain, and nothing else: nothing is compressed, nothing is limited. Reaches any "
            "target, which is why it is the default.",
    "chain": "the operator's Premiere Track Fx, reproduced: +12 dB into a limiter at the ceiling you "
             "set. Sounds like a stitched episode, and cannot peak above that ceiling less the "
             "limiter's own 3 dB.",
    "ceiling": "the gain to the target, then a limiter at the target. For lifting quiet material hard: "
               "it is the only one that never clips while it lifts.",
}

#: The default target, in dBFS.
#:
#: **−6.0** and not −1.0, which is where a *mastering* engineer puts a delivery master. This product's
#: siblings are an editor's tools: `TheStitcher`'s chain ends in a limiter with a −6 dBFS ceiling, so
#: every episode it produces peaks at −6, and a normalizer that made files 5 dB hotter than the
#: episodes they sit next to would be a normalizer that broke the set it was built for. −6 is
#: therefore the default, and −1 is one keystroke away for anyone delivering to someone who wants it.
DEFAULT_TARGET_DBFS = -6.0

#: The range the target may be asked for in. Below −24 dBFS nothing is usable in an edit; above 0 dBFS
#: the request is for a file that clips by construction.
TARGET_RANGE = (-24.0, 0.0)

#: How far the measured output peak may sit from the target before it is a failure rather than the
#: measurement's own resolution.
#:
#: ## Why this is one and a half decibels and not the instrument's last digit
#:
#: `volumedetect` reads to 0.1 dB, so a naive tolerance would be 0.3. That number would make this product
#: fail its own runs, because the delivered peak is not a copy of anything — it is the result of **one
#: lossy encode**, and AAC's decode rings past the samples it was given by an amount that depends on the
#: signal rather than on its level.
#:
#: Measured, on a 1 kHz tone at a −6.0 dBFS target:
#:
#: | What | Peak |
#: |---|---|
#: | the master PCM handed to the encoder | **−6.00 dBFS** |
#: | one AAC encode, decoded | **−5.40** (+0.60) |
#: | corrected by 0.60 and encoded again | **−6.30** (−0.30) |
#:
#: The correction converges — the second error is half the first — but it does not vanish, because the
#: overshoot at one level is not the same figure as at another. Nothing upstream can fix that: it happens
#: inside the decoder, after every filter and every gain.
#:
#: ## The budget, which is why the chain raised it from one to one and a half
#:
#: A run corrects once, so one overshoot of error is unavoidable. A `chain` run adds a second term that a
#: `gain` run does not have: the out fader is derived from a measurement of the chain's output *before*
#: encoding, and the AAC ring of the master's own encode is then unmeasured. The two together were
#: measured across four source levels, three drives and three ceilings — 36 combinations — and the worst
#: case was **1.30 dB**, on material so quiet and so lightly driven that the limiter never engaged and the
#: chain was a plain fader. Everything else was inside half a decibel, and most rows were exact.
#:
#: One and a half decibels catches anything a person would call wrong — a target of −6.0 delivered at
#: −3.0 fails, and a chain that clipped fails by far more than this — while not reporting a codec's own
#: non-linearity, plus one unmeasured pass, as a defect in the run.
PEAK_TOLERANCE_DB = 1.5

#: The sound files a run can also write, in the order they have to be written.
#:
#: The order is a dependency and not a preference. `.mp3` is encoded from the `.wav` this same run
#: writes, so the uncompressed one has to exist first — and encoding the MP3 from the WAV rather than
#: from the finished file a second time is what keeps the two exports *the same sound*: one decode of
#: the normalized file, and the compressed copy is that decode, not a second one that could differ from
#: it in a rounding nobody would ever look for.
AUDIO_FORMATS: tuple[str, ...] = (".wav", ".mp3")

#: The bitrate the MP3 is written at. 320 kbps is the highest the format has: the operator asked for a
#: copy of the sound and not a smaller one, and a lossy copy at the format's ceiling is a copy that
#: cannot be the reason an export is rejected.
MP3_BITRATE = "320k"

#: Where the normalized file goes when the operator has not said, and what tells it apart from its
#: source. `talk.mp4` -> `talk - normalized.mp4`, in the source's own folder.
#:
#: **Not in place.** A normalizer that replaced its input would be a normalizer with no undo: the
#: measured source peak is the only record of what the file used to be, and it lives in this run's
#: report, which the operator closes. The suffix is a default and never a lock — the field is theirs
#: from the moment they type in it.
SUFFIX = " - normalized"

#: What each kind of source container is written as, and with which ffmpeg muxer.
#:
#: ## Why the extension and the muxer are one decision
#:
#: The audio codec this product writes is AAC, because that is what the sibling writes and because
#: every player that has a picture to show can play it. AAC belongs in the MP4 family, is accepted in
#: Matroska, and is **not** accepted beside a video in `.webm` — which is VP8/VP9/AV1 with Vorbis or
#: Opus and refuses anything else. A source whose container cannot hold the new sound is therefore
#: written to one that can, rather than being refused: the alternative is telling an operator that a
#: file they have in front of them cannot be normalized because of the box it came in.
#:
#: ## Why an extension alone is not enough, which was measured
#:
#: The first version of this table mapped a `.wav` source to a `.wav` output, on the reasoning that
#: WAV is the one container that can hold anything. It cannot: ffmpeg's WAV muxer accepts AAC, writes
#: a *valid WAV header*, and stores ADTS frames in the data chunk. `ffprobe` reports it happily —
#: `format_name: wav`, one `aac` stream, the right duration — and the first sample decode fails with
#: `decode_band_types: Input buffer exhausted before END element found`, because AAC in WAV has no
#: defined framing. A run that called that file a success would be a run that reported a number and
#: delivered something no player can open.
#:
#: So the muxer is named explicitly rather than inferred from the extension, and `verify` checks that
#: the finished file's `format_name` is one the muxer names. Both faults — a mismatched muxer and an
#: extension that lies about what is inside — are invisible in a duration and in an exit code.
#:
#: Keyed by the *source's* extension. A container that is not here falls to the default, which is
#: Matroska for a file with a picture and `.m4a` for one without, because those two are the containers
#: that accept every codec this product keeps.
CONTAINERS: dict[str, tuple[str, str, str]] = {
    # source ext: (output ext, ffmpeg muxer, what the output is called)
    ".mp4": (".mp4", "mp4", "MP4"),
    ".m4v": (".m4v", "mp4", "MP4"),
    ".mov": (".mov", "mov", "QuickTime"),
    ".mkv": (".mkv", "matroska", "Matroska"),
    ".mka": (".mka", "matroska", "Matroska audio"),
    ".avi": (".avi", "avi", "AVI"),
    ".ts": (".ts", "mpegts", "MPEG-TS"),
    ".m2ts": (".m2ts", "mpegts", "MPEG-TS"),
    ".mts": (".mts", "mpegts", "MPEG-TS"),
    ".flv": (".flv", "flv", "FLV"),
    ".3gp": (".3gp", "3gp", "3GP"),
    ".mpg": (".mpg", "mpeg", "MPEG-PS"),
    ".mpeg": (".mpg", "mpeg", "MPEG-PS"),
    # Containers that cannot hold AAC beside the streams they have, and the one that can.
    ".webm": (".mkv", "matroska", "Matroska"),
    ".wmv": (".mkv", "matroska", "Matroska"),
    ".ogv": (".mkv", "matroska", "Matroska"),
    ".asf": (".mkv", "matroska", "Matroska"),
    ".vob": (".mkv", "matroska", "Matroska"),
}

#: What a **sound-only** source is written as. A different table because the answer is different: a
#: `.mp4` whose picture was dropped is not a video file any more, and an `.m4a` is what a podcast host,
#: a transcription service and every player on the machine expect to be handed.
AUDIO_ONLY: dict[str, tuple[str, str, str]] = {
    ".mp4": (".m4a", "ipod", "MP4 audio"),
    ".m4v": (".m4a", "ipod", "MP4 audio"),
    ".mov": (".m4a", "ipod", "MP4 audio"),
    ".m4a": (".m4a", "ipod", "MP4 audio"),
    ".m4b": (".m4a", "ipod", "MP4 audio"),
    ".3gp": (".m4a", "ipod", "MP4 audio"),
    ".aac": (".m4a", "ipod", "MP4 audio"),
    ".mkv": (".mka", "matroska", "Matroska audio"),
    ".mka": (".mka", "matroska", "Matroska audio"),
    ".avi": (".mka", "matroska", "Matroska audio"),
    ".ts": (".mka", "matroska", "Matroska audio"),
    ".m2ts": (".mka", "matroska", "Matroska audio"),
    ".mts": (".mka", "matroska", "Matroska audio"),
    ".flv": (".mka", "matroska", "Matroska audio"),
    ".mpg": (".mka", "matroska", "Matroska audio"),
    ".mpeg": (".mka", "matroska", "Matroska audio"),
    ".wmv": (".mka", "matroska", "Matroska audio"),
    ".webm": (".mka", "matroska", "Matroska audio"),
    ".ogv": (".mka", "matroska", "Matroska audio"),
    ".asf": (".mka", "matroska", "Matroska audio"),
    ".vob": (".mka", "matroska", "Matroska audio"),
    ".wav": (".m4a", "ipod", "MP4 audio"),
    ".mp3": (".m4a", "ipod", "MP4 audio"),
    ".flac": (".m4a", "ipod", "MP4 audio"),
    ".ogg": (".m4a", "ipod", "MP4 audio"),
    ".opus": (".m4a", "ipod", "MP4 audio"),
    ".aiff": (".m4a", "ipod", "MP4 audio"),
    ".aif": (".m4a", "ipod", "MP4 audio"),
    ".wma": (".m4a", "ipod", "MP4 audio"),
}

#: The formats each muxer actually writes, as `ffprobe`'s own `format_name` spells them. Used by
#: `verify` for one check: that the file on disk is the container it was asked for, rather than a valid
#: header wrapped around a codec the container has no framing for. See `CONTAINERS` for the measured
#: defect that check exists to catch.
MUXER_FORMATS: dict[str, tuple[str, ...]] = {
    "mp4": ("mov", "mp4", "m4a", "3gp", "3g2", "mj2", "ismv", "ipod"),
    "ipod": ("mov", "mp4", "m4a", "3gp", "3g2", "mj2", "ismv", "ipod"),
    "mov": ("mov", "mp4", "m4a", "3gp", "3g2", "mj2", "ismv"),
    "matroska": ("matroska", "webm"),
    "avi": ("avi",),
    "mpegts": ("mpegts",),
    "flv": ("flv",),
    "3gp": ("3gp", "mov", "mp4"),
    "mpeg": ("mpeg", "mpegvideo"),
}


class NormalizeError(RuntimeError):
    """A refusal the window can act on. `reason` is the stable tag it branches on."""

    reason = "normalize"


class InputRefused(NormalizeError):
    reason = "input"


class NotNormalizable(NormalizeError):
    reason = "shape"


class OutputRefused(NormalizeError):
    reason = "output"


@dataclass(frozen=True)
class NormalizeSpec:
    """One request: a source, and where the normalized copy goes.

    ## Why the source is one named field and not a list

    A list of sources is a *batch*, and a batch is the window's word for it rather than the engine's.
    Each file in a batch is normalized independently — its own peak, its own target, its own output —
    so the engine's unit of work is one file and the window is what puts several of them in a row. A
    `list[Path]` here would make "which file is this log line about" a question the *engine* had to
    answer on every line, when it already answers it once per file.
    """

    source: Path
    output: Path
    #: The level the finished file should peak at, in dBFS. **Negative**, because dBFS has no positive
    #: values that are not clipping.
    target_dbfs: float = DEFAULT_TARGET_DBFS
    strategy: str = DEFAULT_STRATEGY
    #: How hard the material is driven **into the limiter**, in decibels — the operator's Premiere
    #: "make up" field, and +12 dB by default because that is what their timeline has.
    #:
    #: ## Why it is in front of the chain rather than inside it
    #:
    #: `acompressor`'s own `makeup` option is a gain *before its own limiter*, which is exactly where
    #: this belongs — and it cannot express a value below unity, so a source that is too loud for the
    #: target could not be brought down through it. A fader in front of the whole chain can, and it is
    #: the same element the operator's field is. The chain's `makeup` is therefore left at unity and
    #: this is the drive.
    #:
    #: ## What it decides
    #:
    #: The run's gain is what the target asks for (`target − source peak`). This is added **on top of
    #: it**, so it is what drives the material up against the limiter: at 0 dB the chain is transparent
    #: and the target is reached exactly; at +12 dB — the operator's setting — the material is twelve
    #: decibels hotter, the limiter clamps what reaches it, and the file comes out at the limiter's own
    #: working level rather than at the target. That is what a chain with a limiter in it does, and the
    #: plan says which of the two happened before the run rather than after it.
    makeup_db: float = DEFAULT_MAKEUP_DB
    #: The chain's limiter ceiling, in dBFS. −6 is the operator's setting and TheStitcher's.
    #:
    #: It is the level the chain will not let a sample past, and it is the one setting here that decides
    #: what the output can reach: a limiter clamps material driven into it to roughly three decibels
    #: under its nominal ceiling, so **a target above `ceiling_dbfs − 3` is a target the chain cannot
    #: reach** however the gain is arranged. `plan_normalize` says so before the run rather than leaving
    #: it to be found in the file. Raise this field to deliver a hotter file through the chain, or choose
    #: the `gain` strategy, which has no limiter in it.
    ceiling_dbfs: float = DEFAULT_CEILING_DBFS
    #: Applied after the strategy's chain. `0.0` means "the target and nothing else".
    #:
    #: It exists because the target is a *peak* and a peak is one sample: two files at the same peak
    #: can be 6 dB apart in what a listener calls loudness. This trims the perceived level without
    #: changing the peak, and it is the operator's number rather than one this product chose.
    trim_db: float = 0.0
    #: Also write the normalized sound beside the output, once per extension named here.
    #:
    #: Empty by default: these are extra deliverables, not halves of the first one, and a run that
    #: wrote files nobody asked for would leave litter next to a master.
    audio_exports: tuple[str, ...] = ()
    #: The encoder's bitrate for the AAC sound. 192k is what the sibling product writes.
    audio_bitrate: str = "192k"


@dataclass(frozen=True)
class Command:
    """One ffmpeg invocation, as data.

    A list of arguments rather than a string, and a plain value rather than a call, so the whole run
    can be asserted on without starting a process. Every claim this product makes about *copying the
    picture* is a claim about these lists.
    """

    label: str
    args: list[str]
    expected_seconds: float


@dataclass(frozen=True)
class NormalizePlan:
    """Everything the run will do, decided before anything is written.

    Built by :func:`plan_normalize`, which is **pure**: it creates no file, starts no process and reads
    no clock. That is what makes it safe to call as the operator types, which is what lets the window
    state the gain and the target before anyone commits to a run.
    """

    spec: NormalizeSpec
    source: MediaInfo
    #: The source's measured peak, in dBFS. `None` is digital silence.
    source_peak_dbfs: float | None
    source_mean_dbfs: float | None
    #: What the finished file is expected to peak at: the target for a strategy with no limiter, and for
    #: a chain run whatever the limiter's measurement says it will be. See :attr:`expected_peak_dbfs`.
    expected_peak_dbfs: float
    #: The gain the plan can state before anything has run: `target − source peak`, plus the drive. It is
    #: what the window shows while the operator chooses files, and it is what the *stage* pass carries.
    #: The master pass's figure comes from what the stage measured.
    planned_gain_db: float
    measured_chain_peak_dbfs: float | None = None
    #: What the chain moved this file's peak by, once the run has measured it: the chain's own effect,
    #: with the fader's gain *not* included. `None` for a strategy that has no chain.
    #:
    #: It is reported separately from the fader because they are different claims. "The chain lifted
    #: this file 12 dB" is a statement about the operator's timeline; "the fader took it to −6.0 dBFS"
    #: is a statement about the request. A report that adds them into one number cannot be checked
    #: against either.
    measured_chain_gain_db: float | None = None
    #: The peak of the **decoded** sound after the stage pass, when the stage pass encoded to the same
    #: codec the master is written in.
    #:
    #: ## Why this number exists at all, which is a measured fault of the codec
    #:
    #: AAC overshoots on decode. Measured on this machine: a 1 kHz tone written into PCM at exactly
    #: −6.00 dBFS comes back out of `aac` at **−5.40 dBFS**, and the overshoot is not a gain — the mean
    #: level is unchanged, so it is the codec's reconstruction ringing past the sample values it was
    #: given. It cannot be prevented by anything upstream: an `alimiter` set at −6, −5, −4, −3, −2, −1
    #: or 0 dBFS all produced the same −5.4 dBFS on decode, because the limit is applied to samples and
    #: the overshoot happens after them. FLAC, with no such ringing, reproduced −6.00 exactly.
    #:
    #: So a plan that set the fader from the *PCM* peak and promised −6.0 dBFS would be promising a
    #: number 0.6 dB away from the file it delivers — on every file, and looking perfectly correct in
    #: the plan. The stage pass therefore encodes to the master's own codec, and the fader is set from
    #: what comes back out of it.
    measured_decoded_peak_dbfs: float | None = None
    #: The container the sound is written into: its extension, the ffmpeg muxer that writes it, and the
    #: name a person calls it by. Three fields and not one, because they are three different things and
    #: the muxer is the one that must not be inferred from the extension.
    container: str = ""
    muxer: str = ""
    container_label: str = ""
    #: The stream types this run will not carry over, by name. Empty for most files; a `.mp4` with a
    #: subtitle track is the case that fills it.
    dropped_streams: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=())
    cautions: tuple[str, ...] = field(default=())

    @property
    def uses_chain(self) -> bool:
        """Does this run have a filtergraph whose output level has to be measured before the fader?

        Only the `chain` strategy does. The other two are a fader and, for `ceiling`, a limiter: one
        pass from the source, and their output level is a fact about the filters rather than about the
        material.
        """
        return self.spec.strategy == "chain"

    @property
    def chain_out_gain_db(self) -> float:
        """The fader **after** the chain, for the pass being built.

        Zero for the **stage** pass, because a measurement of the chain is a measurement of the chain and
        the correction has to be derived from the uncorrected figure. For the **master** pass it is
        `target − what the stage measured`, which is the whole point of measuring: the limiter's output is
        not its ceiling and not computable from the settings. Measured on a −2.0 dBFS source through the
        operator's +12 dB of make-up, a −6.0 dBFS ceiling delivered −5.10, a −3.0 ceiling delivered
        −8.10, and a −1.0 ceiling delivered −10.10. The plan cannot know which of those it will be.

        This is the only gain in the product that sits after a limiter, and it is safe there because the
        limiter has already bounded its input: the fader's gain is exactly the gap between what the
        limiter delivered and the target, so it can only ever lift a signal that is below full scale up to
        a level that is below full scale.
        """
        if not self.uses_chain or self.measured_decoded_peak_dbfs is None:
            return 0.0
        return round(self.spec.target_dbfs - self.measured_decoded_peak_dbfs, 6)

    @property
    def overshoot_db(self) -> float:
        """How far the **decoder** overshoots the samples it was handed.

        Zero until the run has measured it. It is the difference between the level the stage pass
        contains and the level that comes back out of the codec — AAC's reconstruction ringing past the
        values it was given, which on this machine's ffmpeg is **0.6 dB** on a tone and cannot be
        prevented upstream, because it happens after the samples.

        It is meaningful only because the stage pass and the master pass put the *same* gain in front of
        the encoder: the same operating point, so the overshoot measured at one is the overshoot the
        other will produce. A stage pass that encoded the signal somewhere else would measure a
        different ringing and the correction would be a number about nothing.
        """
        if self.measured_decoded_peak_dbfs is None or self.measured_chain_peak_dbfs is None:
            return 0.0
        return round(self.measured_chain_peak_dbfs - self.measured_decoded_peak_dbfs, 4)

    @property
    def nominal_gain_db(self) -> float:
        """The fader that would put the **source** on the target: `target − source peak`.

        The plan's own arithmetic, and the only figure available before anything has been measured. For
        a `chain` run it is *not* the gain the chain's own operating point needs — see
        :attr:`total_gain_db` for the +12 dB the chain's make-up adds and the clipping that follows from
        ignoring it.
        """
        if self.source_peak_dbfs is None:
            return 0.0
        return round(self.spec.target_dbfs - self.source_peak_dbfs, 4)

    def working_gain_db(self, residual: float = 0.0) -> float:
        """The front fader: what the target asks for, plus the drive into the chain, plus a correction.

        Three terms, and each is a different decision:

        * **`target − source peak`** — the run's own arithmetic, and the whole story for the strategies
          with no chain in them;
        * **`spec.makeup_db`** — the operator's drive, applied on top so that the chain is transparent at
          0 dB and pinned to its limiter at +12 dB. See `NormalizeSpec.makeup_db`;
        * **`residual`** — what the last measurement said was still to correct, which is how the master
          pass lands on the target through an encoder.

        The two passes of a run have to agree on this to more than a report's precision, which is why it
        is one function rather than the same arithmetic written twice.
        """
        if self.source_peak_dbfs is None:
            return 0.0
        drive = self.spec.makeup_db if self.uses_chain else 0.0
        return round(self.spec.target_dbfs - self.source_peak_dbfs + drive + residual, 6)

    @property
    def master_gain_db(self) -> float:
        """The gain the **master** pass applies in front of the strategy, at full precision.

        For a strategy with no chain this is what the target asks for, corrected by what the stage
        measured — the two passes are the same graph and the correction is the whole of the difference.

        **For `chain` it is the stage's own figure, unchanged**, and that is deliberate: the master has to
        run the chain at the *same drive* the stage measured it at, or the measurement describes a
        different signal. The correction for a chain run is applied by the **out fader** instead — the one
        after the limiter — because the limiter clamps whatever reaches it, so a correction added in front
        of it changes nothing at all. That was a real defect: the residual was being folded into the front
        gain and the delivered level sat a decibel low on every file, with the out fader computed
        correctly on paper and the drive quietly absorbing it.
        """
        if self.uses_chain:
            return self.stage_gain_db
        if self.source_peak_dbfs is None:
            return 0.0
        residual = 0.0
        if self.measured_decoded_peak_dbfs is not None:
            residual = self.spec.target_dbfs - self.measured_decoded_peak_dbfs
        return self.working_gain_db(residual)

    @property
    def total_gain_db(self) -> float:
        """**The fader the master pass applies**, which is the one number the whole run turns on.

        ## How it is arrived at, and why it is not `target − source peak`

        Two encodes decide it, and both happen at the same operating point:

        * the **stage pass** applies the strategy and the gain `working_gain_db()`, encodes to the
          master's own codec, and measures what the decoder hands back;
        * the master pass applies the same strategy and the gain that takes *that measured level* to the
          target.

        Written out, with `A` the source's peak, `V` the nominal gain, `C(x)` the whole encode-and-ring
        path and `o` its overshoot — the same over the small range between the two gains:

            stage:   decoded(A) = C(A + V)  = A + V + o
            master:  decoded(B) = C(A + G)  = A + G + o
            want:    decoded(B) = t
            ⇒        G          = t − A − o = V + (t − (A + V + o)) = V + (t − decoded(A))

        So the correction is `target − the level one encode of this material actually produced`, which is
        a measurement rather than a model of the codec. On the reference tone: `V` = +21.1,
        `decoded(A)` = −5.4, and `G` = +21.1 + (−0.6) = **+20.5 dB**, which delivers **−6.0 dBFS**.

        ## Why the phase matters, which is where the first two designs went wrong

        `V` is applied **after** the chain, and the chain's own make-up is applied **before** it. The
        first design put `target − source peak` in front of the chain and delivered 0.0 dBFS, clipped,
        because the chain's make-up then drove a signal that was already at full scale. The second ran
        the stage pass with no gain at all and produced a correction of +9.7 dB that was mostly the gain
        for the stage rather than overshoot from the codec. Both were arithmetic on the wrong pair of
        numbers; this one is one pair of numbers measured at one operating point.

        Before any measurement the nominal gain is the honest answer and the window says so.
        """
        return round(self.master_gain_db, 4)

    @property
    def gain_text(self) -> str:
        return f"{self.total_gain_db:+.2f} dB"

    @property
    def gain_db(self) -> float:
        """The same number as :attr:`total_gain_db`, under the name the verifier and the report use."""
        return self.total_gain_db

    @property
    def stage_gain_db(self) -> float:
        """The gain the **stage** pass applies in front of the chain: the target's own arithmetic.

        Not capped: the limiter downstream is what keeps the level in range, and applying less than the
        target asks for would be the run deciding on its own to miss. `spec.makeup_db` reaches the
        command as the limiter's drive, but the gain that decides where the file lands is this one.
        """
        return self.working_gain_db()

    @property
    def gain_is_measured(self) -> bool:
        """True once a run has measured the codec for this file, which is what decides the fader.

        `False` for a plan that has been built but not started, **for every strategy** — including the
        ones whose gain the plan can work out in full, because the plan's arithmetic is the front fader
        and the file's peak is one encode away from it. The window shows the planned figure with a note
        that it is provisional rather than hiding the row: a plan that showed nothing where a gain belongs
        would read as a plan that had failed.
        """
        return self.measured_decoded_peak_dbfs is not None

    def with_measurements(
        self, chain_peak_dbfs: float | None, decoded_peak_dbfs: float | None
    ) -> NormalizePlan:
        """This plan, with what the chain and the codec actually did to this material.

        A new value rather than a mutation, because `NormalizePlan` is frozen: the plan the window was
        shown and the plan the run is carrying out are then two values, and the outcome can report both
        rather than pretending the second was always the first.

        The chain's own gain is computed here as well, because this is the only place that has both
        numbers: the source's peak and the peak after the chain. It is `None` when either end is
        digital silence, where a difference in decibels is not a difference of anything.
        """
        moved: float | None = None
        if self.source_peak_dbfs is not None and chain_peak_dbfs is not None:
            moved = round(chain_peak_dbfs - self.source_peak_dbfs, 4)
        return replace(
            self,
            measured_chain_peak_dbfs=chain_peak_dbfs,
            measured_decoded_peak_dbfs=decoded_peak_dbfs,
            measured_chain_gain_db=moved,
        )

    @property
    def keeps_picture(self) -> bool:
        return self.source.has_picture

    @property
    def duration(self) -> float:
        return self.source.duration

    @property
    def audio_paths(self) -> tuple[Path, ...]:
        """Where the normalized sound is also written, in the order it is written.

        The master's own stem with each requested extension, in the master's own folder — so every
        deliverable of one run is one name apart and none of them has to be described to be found.
        """
        parent, stem = self.output.parent, self.output.stem
        return tuple(parent / f"{stem}{extension}" for extension in self.spec.audio_exports)

    @property
    def output(self) -> Path:
        """The name the master is published under, with the container's own extension.

        The extension is the *container's*, and the container is decided by :func:`container_for` because
        it depends on whether the source's own can hold an AAC sound: a `.wav` source is written as an
        `.m4a`, a `.webm` source as an `.mkv`. Everything else about the name is the request's.

        This property is also what keeps :attr:`published_as` and the two sound exports on the *same*
        stem as the deliverable: there is one spelling of "the name this run writes", and it is this one.
        """
        return self.spec.output.with_suffix(self.container)

    @property
    def published_as(self) -> Path:
        """Where the run writes before the rename that publishes it.

        `<name>.part.mp4` and not `<name>.mp4.part`, because the muxer chooses its format from the
        extension and an unrecognised one is a hard failure at the last step of a long run. The muxer
        is named explicitly in the command as well, so this is belt and braces rather than the only
        thing standing between the run and a `.part` file nobody can open.
        """
        output = self.output
        return output.with_name(output.stem + ".part" + output.suffix)

    @property
    def gain_text(self) -> str:
        return f"{self.total_gain_db:+.2f} dB"

    @property
    def target_text(self) -> str:
        return f"{self.spec.target_dbfs:.1f} dBFS"


# ---------------------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------------------

def available_output(spec: NormalizeSpec, container: str) -> Path:
    """Where the master goes: the requested name, with the container's extension, and never over a file.

    ## Why the extension is put on *here* and not by the caller

    The container is decided by :func:`container_for`, which needs the probed file — so a caller that
    built a full path before the probe would be guessing an extension. What a caller supplies is the
    folder and the stem (`output_for`); this attaches what the container turned out to be.

    ## Why a name that is taken steps aside instead of refusing

    The MP4 in the sibling product is refused when its name is occupied, because that name is an episode
    somebody stitched and there is exactly one of it. A *normalized copy* is not that: it is derived
    from a file that is still sitting right there, it can be made again in the same number of seconds,
    and the common case of pressing the button twice is not a mistake worth a red sentence. So the name
    walks: `talk - normalized.m4a`, then `talk - normalized 2.m4a`, then `3`.

    The rule is total in the other direction. A path this run did **not** choose is never written over,
    which is why `run_normalize` checks the destination again at the moment of the rename.
    """
    requested = spec.output.with_suffix(container)
    if not requested.exists():
        return requested
    index = 2
    while True:
        candidate = requested.with_name(f"{requested.stem} {index}{container}")
        if not candidate.exists():
            return candidate
        index += 1


def output_for(source: Path, extension: str | None = None) -> Path:
    """Where a normalized copy of this file goes: beside it, with the suffix, and never over it.

    The **whole** name is kept and the suffix inserted before the source's own extension —
    `talk.en.mp4` becomes `talk.en - normalized.mp4` — because :func:`available_output` needs a stem it
    can attach the container to, and a stem is what is left when the extension comes off. A name with
    the extension already stripped cannot be told from a name whose last word happens to contain a dot,
    which is exactly the bug this shape avoids.

    `extension` is for the one caller that has already probed the file and knows the container; the route
    and the engine leave it out and let :func:`available_output` decide.
    """
    stem = source.stem + SUFFIX
    tail = source.suffix if extension is None else extension
    candidate = source.with_name(f"{stem}{tail}")
    index = 2
    while candidate.exists():
        candidate = source.with_name(f"{stem} {index}{tail}")
        index += 1
    return candidate


def container_for(source: MediaInfo) -> tuple[str, str, str]:
    """``(extension, muxer, label)`` for the normalized copy of this file.

    The source's own container when the source's container can hold an AAC sound, because that is the
    extension the operator already has a habit for and every other stream in the file can come along.
    When it cannot — `.webm`, `.wmv`, an Ogg, or a `.wav` whose sound is being re-encoded — the file is
    written to a container that can, and the plan says so before the run.

    The **muxer** is returned rather than inferred from the extension, because inferring it is the
    defect `CONTAINERS` documents: ffmpeg's WAV muxer will happily write AAC into a valid WAV header
    and produce a file that probes correctly and decodes to nothing.
    """
    extension = source.path.suffix.lower()
    table = AUDIO_ONLY if not source.has_picture else CONTAINERS
    fallback = (".m4a", "ipod", "MP4 audio") if not source.has_picture else (".mkv", "matroska", "Matroska")
    return table.get(extension, fallback)


def _droppable_check(source: MediaInfo, container: str) -> tuple[str, ...]:
    """Which streams the output container will not carry, by name.

    Audio-only MP4-family files carry one sound and nothing else, so a `.mov` with a timecode track
    loses it. That is worth a sentence rather than a surprise: the stream was in the file the operator
    handed over, and this product's whole promise about the rest of the file is that it comes out
    unchanged.
    """
    if container in (".mka", ".mkv"):
        # Matroska carries essentially anything ffmpeg can put in a stream, so nothing is named.
        return ()
    if container in (".m4a",):
        keep = {"audio"}
    else:
        keep = {"video", "audio"}
    return tuple(f"{kind}:{codec}" for kind, codec in source.streams if kind not in keep)


def plan_normalize(
    spec: NormalizeSpec,
    source: MediaInfo,
    peak_dbfs: float | None,
    mean_dbfs: float | None,
) -> NormalizePlan:
    """Decide the whole run before anything is written.

    The peak arrives as an argument rather than being measured here, for the reason the sibling
    product's plan takes its black-frame counts as arguments: a plan that measured would be a plan that
    started a process, and the whole value of this type is that it can be built while somebody is
    typing.
    """
    if spec.strategy not in STRATEGIES:
        raise InputRefused(
            f"'{spec.strategy}' is not a normalizing strategy. The ones there are: "
            + ", ".join(sorted(STRATEGIES))
        )

    low, high = TARGET_RANGE
    if not low <= spec.target_dbfs <= high:
        raise InputRefused(
            f"the target is {spec.target_dbfs:.1f} dBFS, which is outside the range this product "
            f"works in ({low:.0f} to {high:.0f} dBFS). A level below {low:.0f} is unusable in an "
            f"edit; a level above {high:.0f} is a file that clips by construction."
        )

    if abs(spec.trim_db) > 24.0:
        raise InputRefused(
            f"the trim is {spec.trim_db:+.1f} dB, and the range it is allowed in is −24 to +24. A "
            f"larger number than that is a target that was asked for in the wrong field."
        )

    low_makeup, high_makeup = MAKEUP_RANGE
    if not low_makeup <= spec.makeup_db <= high_makeup:
        raise InputRefused(
            f"the make-up gain is {spec.makeup_db:+.1f} dB, which is outside the range this chain "
            f"works in ({low_makeup:+.0f} to {high_makeup:+.0f} dB). The ceiling of +36 dB is ffmpeg's "
            f"own limit on the option (`makeup=64`), and below −12 dB the chain is an attenuator, which "
            f"is what the target field is for."
        )

    low_ceiling, high_ceiling = CEILING_RANGE
    if not low_ceiling <= spec.ceiling_dbfs <= high_ceiling:
        raise InputRefused(
            f"the limiter ceiling is {spec.ceiling_dbfs:.1f} dBFS, which is outside the range this "
            f"chain works in ({low_ceiling:.0f} to {high_ceiling:.0f} dBFS). Above {high_ceiling:.0f} "
            f"is not a ceiling; below {low_ceiling:.0f} the chain is squashing everything to a whisper "
            f"for no reason a person would have."
        )

    unknown = [extension for extension in spec.audio_exports if extension not in AUDIO_FORMATS]
    if unknown:
        raise InputRefused(
            "the sound files were asked for in a format nothing here can write: "
            + ", ".join(unknown)
            + ". The ones it can write are "
            + ", ".join(AUDIO_FORMATS)
            + "."
        )
    # A dependency, refused rather than served. `.mp3` is encoded from the `.wav` the same run writes,
    # so a `.mp3` on its own is a request for a file to be made out of one that will not exist — and
    # the command builder would build exactly that and fail at the last pass of a run. Refusing costs
    # the operator one checkbox and is answered before anything is written.
    for extension, needs in ((".mp3", ".wav"),):
        if extension in spec.audio_exports and needs not in spec.audio_exports:
            raise InputRefused(
                f"'{extension}' is encoded from '{needs}', so '{needs}' is asked for with it rather "
                f"than instead of it. Ask for "
                + " and ".join(f"'{each}'" for each in AUDIO_FORMATS)
                + f", or for '{needs}' alone."
            )

    if source.audio is None:
        raise NotNormalizable(f"{source.name} has no audio stream, so there is nothing to normalize")

    if source.duration <= 0:
        raise NotNormalizable(
            f"{source.name} does not declare a duration, so there is no length to cut the result to"
        )

    notes: list[str] = []
    cautions: list[str] = []

    # ---- What the two chain figures will do to this file -----------------------------------
    if spec.strategy == "chain":
        # The highest peak the chain can deliver. The limiter clamps hard-driven material to about three
        # decibels under its nominal ceiling, and the run's gain cannot lift the result past that, so a
        # target above it is a target the chain cannot reach. Better said before the run than found out
        # from the file.
        reachable = round(spec.ceiling_dbfs - LIMITER_OFFSET_DB, 2)
        if spec.target_dbfs > reachable + 0.05:
            cautions.append(
                f"the target is {spec.target_dbfs:.1f} dBFS and the chain's limiter is at "
                f"{spec.ceiling_dbfs:.1f} dBFS, which clamps material into it to about "
                f"{reachable:.1f} dBFS. This file will peak near {reachable:.1f}. Raise the ceiling, or "
                f"use the gain strategy, which has no limiter."
            )

    container, muxer, label = container_for(source)
    # The destination is settled here, once the container is known, and it is settled against the
    # filesystem: a name that is taken steps aside rather than refusing. This is the *only* place that
    # decision is made, so the path the window shows is the path the run writes to.
    spec = replace(spec, output=available_output(spec, container))
    original = source.path.suffix.lower()
    if container != original:
        if source.has_picture:
            cautions.append(
                f"the sound is re-encoded as AAC, and a '{original or 'unknown'}' container will not "
                f"carry AAC beside this picture, so the normalized file is written as "
                f"{label} ('{container}') instead. The picture is still copied, not re-encoded."
            )
        else:
            cautions.append(
                f"the sound is re-encoded as AAC, and a '{original or 'unknown'}' file cannot hold it "
                f"— AAC needs the framing a '{original or 'unknown'}' container has no place for, and "
                f"the file it produces probes correctly and plays as nothing. The normalized sound is "
                f"written as {label} ('{container}') instead."
            )

    dropped = _droppable_check(source, container)
    if dropped:
        notes.append(
            "the output container does not carry every stream the source has, so these are not in it: "
            + ", ".join(dropped)
            + ". The picture and the sound are."
        )

    if peak_dbfs is None:
        # Digital silence. There is no gain that makes silence audible, and inventing one would be the
        # product making up a number; the honest answer is a gain of zero and a sentence.
        gain = 0.0
        expected = spec.target_dbfs
        notes.append(
            "this file's sound is digital silence — no sample in it is above the noise floor of the "
            "format — so no gain is applied. It will be written with its sound unchanged."
        )
    else:
        expected = round(spec.target_dbfs, 4)
        if spec.strategy == "chain":
            # The chain is going to change this file's level by an amount that depends on the chain's
            # own behaviour — it is a +12 dB drive into a limiter whose real ceiling is three decibels
            # below the number it names. The plan can state the *target*, and it cannot state the gain,
            # so it does not pretend to: the run measures the chain and the fader is set from that. See
            # `sound_graph`.
            gain = round(spec.target_dbfs - peak_dbfs + spec.trim_db, 4)
            notes.append(
                "the dynamics chain's effect on this file's level is measured during the run rather "
                "than assumed, so the gain shown after the run is the one that was actually applied."
            )
        else:
            gain = round(spec.target_dbfs - peak_dbfs + spec.trim_db, 4)

        if gain > 12.0 and spec.strategy != "chain":
            cautions.append(
                f"the gain is {gain:+.2f} dB, which is a lot: this file peaks at {peak_dbfs:.2f} dBFS, "
                f"far below the {spec.target_dbfs:.1f} dBFS target. Whatever noise floor it has is "
                f"lifted by the same amount, and it is worth listening to the result rather than "
                f"trusting the number."
            )
        if gain < -12.0 and spec.strategy != "ceiling":
            notes.append(
                f"the gain is {gain:+.2f} dB — this file is loud for its target, so it is being turned "
                f"down rather than lifted."
            )

    if source.has_picture:
        notes.append(
            f"the picture is stream-copied, so this is a remux plus one audio encode rather than a "
            f"re-encode: {source.video.width}x{source.video.height} {source.video.codec} is written "
            f"out exactly as it came in."
        )
    else:
        notes.append("this file has no picture, so the normalized copy is sound only.")

    notes.append(
        f"the sound is written at the source's own {source.audio.sample_rate} Hz and "
        f"{source.audio.channels}ch — nothing here resamples or remixes."
    )

    profiles = {stream for kind, stream in source.streams if kind == "video"}
    if len(profiles) > 1:
        notes.append("the source has more than one video stream; each is copied as it is.")

    if source.audio.sample_rate not in (44100, 48000, 88200, 96000):
        cautions.append(
            f"the source's sample rate is {source.audio.sample_rate} Hz, which is not one of the rates "
            f"AAC is normally written at. The sound is kept at its own rate; if a player refuses it, "
            f"the rate is the reason."
        )

    return NormalizePlan(
        spec=spec,
        source=source,
        source_peak_dbfs=peak_dbfs,
        source_mean_dbfs=mean_dbfs,
        expected_peak_dbfs=expected,
        planned_gain_db=gain,
        container=container,
        muxer=muxer,
        container_label=label,
        dropped_streams=dropped,
        notes=tuple(notes),
        cautions=tuple(cautions),
    )

# ---------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------

def sound_graph(plan: NormalizePlan, stage: str = "master") -> str:
    """The filtergraph that produces the normalized sound.

    ## The order, which took three designs to get right

    Fixed, and the same for both passes:

    **the strategy, with the fader inside it.**

    For `chain` that means the operator's whole chain — compressor, limiter — with the make-up gain
    replaced by the gain this run needs. For `gain` it means one fader. For `ceiling` it means the fader
    and then a limiter at the target.

    ## Why the fader is inside the chain and not after it, which is the measured part

    The obvious shape is `<chain>,volume=<gain>` — the operator's settings untouched and one fader on
    the end. It is wrong, and it is wrong by a lot: **`alimiter` is a hard ceiling at the level it is
    given**, so a chain left at the operator's −6 dBFS cannot deliver a file that peaks *below* −6 no
    matter what follows it. Measured: a source at −40.5 dBFS run through
    `acompressor…makeup=3.9812,alimiter=limit=0.5012` and then `volume=+22.5dB` delivers **−6.1 dBFS**
    against a −9.0 dBFS target, and no gain after the limiter changes that.

    Putting the fader *before* the chain has the opposite fault: the chain's own +12 dB make-up then
    drives a signal that is already at full scale, and the result is **0.0 dBFS, clipped** — measured on
    a −27.1 dBFS source asking for −6.0.

    So the chain's make-up gain **is** this run's gain. The limiter stays where the operator put it, at
    its ceiling, doing the job a limiter does; the fader is what the target asks for; and the two passes
    are the same graph at the same level, which is what makes the codec measurement transferable.

    ## What this gives up, said plainly

    The operator's `Compressor → make up: 12 dB` and the fader are the same element: a **flat** +12 dB
    *is* what their settings describe (the compressor runs at ratio 1 and does no gain reduction), so
    what is preserved is their limiter, their compressor's shape and their intent. What is lost is the
    ability to drive the limiter *harder than the target requires*, which is what a fixed +12 dB make-up
    with a target below the ceiling would do — and which is also what makes a chain deliver −6.1 dBFS
    for a −9.0 dBFS target. `spec.makeup_db` therefore sets the **ceiling of the fader**, not a gain
    applied on top of it: the run will not drive harder than the operator's make-up allows, and it will
    drive less when the target asks for less.

    ## The two passes

    ``stage`` and ``master`` differ in one number: the gain. The stage pass carries
    :attr:`NormalizePlan.total_gain_db` as it stands before the codec has been measured; the master
    carries it corrected by what the stage found. Same chain, same limiter, same operating point, so the
    ringing one encode adds is the ringing the other will add.

    ## Why nothing else is in the graph

    No resampler, no channel mixer, no ``dynaudnorm``, no EQ. Every one of them would be this product
    deciding something the operator did not ask for, and each would make the output's peak depend on its
    own behaviour rather than on the measurement. The graph is short on purpose, and a test asserts that
    the only filters in it are the ones above.
    """
    gain = plan.stage_gain_db if stage == "stage" else plan.master_gain_db

    if plan.spec.strategy != "chain":
        parts = [f"volume={gain:.6f}dB"]
        if plan.spec.strategy == "ceiling":
            # This strategy may be lifting quiet material past 0 dBFS, and its limiter at the target is
            # what makes "never clips while it lifts" true.
            parts.append(f"alimiter=limit={_linear(plan.spec.target_dbfs)}:release=50:level=0")
        return ",".join(parts)

    # ---- The chain --------------------------------------------------------------------------
    #
    # Three elements, and every one of them is there because a measurement put it there:
    #
    #   1. the run's front fader — the drive, in front of the limiter, where the operator's own make-up
    #      field is. It is the only element that can change how hard the material hits the limiter, and
    #      it is where attenuation is possible: `acompressor`'s `makeup` option cannot go below 1.0;
    #   2. the operator's chain at the ceiling the request names;
    #   3. the out fader, which is what puts the result on the target. It is after the limiter, and it is
    #      the only gain this product places there — safe, because the limiter has already bounded what
    #      reaches it. See `chain_graph` for why there is no formula for it.
    # The **stage** pass carries no out fader, because a measurement of the chain is a measurement of the
    # chain and the correction has to be derived from the uncorrected figure. The **master** carries what
    # that measurement bought. See `chain_out_gain_db`.
    out_gain = 0.0 if stage == "stage" else plan.chain_out_gain_db
    return chain_graph(plan.spec.ceiling_dbfs, gain, out_gain)


def master_command(plan: NormalizePlan, master: Path) -> Command:
    """The one command that puts the finished picture and the finished sound into one file.

    The picture comes from the **source** and is stream-copied; the sound comes from `master`, which is
    the file the fader wrote. Two inputs, and the mapping says which is which — a command that took its
    audio from the source would undo the whole run, and a command that took its picture from `master`
    would not have one.
    """
    spec = plan.spec
    source = plan.source

    args = [tool("ffmpeg"), "-y", "-v", "error", "-i", str(source.path), "-i", str(master)]

    if source.has_picture:
        # Every video stream, copied. `-map 0:v` rather than `0:v:0` because a source with two angles
        # or a second video track is a source whose other tracks this product has no business
        # dropping, and a copy costs nothing.
        args += ["-map", "0:v", "-c:v", "copy"]

    # The sound, already level-set, encoded once. `-map 1:a:0` — from the second input, which is the
    # fader's output rather than the source.
    args += ["-map", "1:a:0", "-c:a", "aac", "-b:a", spec.audio_bitrate]

    # **The muxer, named.** Not inferred from the extension: ffmpeg picks its muxer from the extension
    # and will accept a codec the container cannot frame, which is how a WAV full of ADTS AAC gets
    # written and called a success. See `CONTAINERS`.
    args += ["-f", plan.muxer]

    if plan.muxer in ("mp4", "mov", "ipod"):
        # The MP4 family: a trailer-less file must be seekable while it is being watched, and
        # `-movflags` is accepted by nothing else.
        args += ["-movflags", "+faststart"]

    args += ["-map_metadata", "0", "-map_chapters", "0", str(plan.published_as)]

    return Command(
        label=(
            "copy the picture and write the sound"
            if source.has_picture
            else "write the normalized sound"
        ),
        args=args,
        expected_seconds=plan.duration,
    )


def normalize_commands(plan: NormalizePlan, work: Path) -> list[Command]:
    """Every command the run will execute, in the order it can know about them, as data.

    Returned rather than executed so that a test can assert what will happen — that the picture is
    always copied, that the muxer is always named, that the sound is always re-encoded — without a
    process, a source file, or a minute of waiting.

    ## Why the chain is not in this list

    For a `chain` run the fader's value is unknown until the chain has been applied and *measured*, and
    a command list is a thing built before anything runs. So this returns the commands whose arguments
    are knowable from the plan — the fader and the mux — and `run_normalize` prepends the chain pass for
    the strategy that has one. A list that pretended to hold all three would have to carry a placeholder
    gain, and a placeholder in an argument list is a wrong argument list.

    ## Why the sound is built as a file first

    The fader writes `master.wav`, and then one command muxes it with the copied picture. A single pass
    that filtered the audio and copied the video at once would have to decide the fader's value *before*
    the chain had been measured, which is the three-decibel error `sound_graph` documents.
    """
    source = plan.source
    args = [tool("ffmpeg"), "-y", "-v", "error", "-i", str(source.path)]
    args += ["-map", "0:a:0", "-af", sound_graph(plan, "master")]
    args += ["-c:a", "pcm_s24le", str(work / "master.wav")]

    return [
        Command(
            label=f"set the level to {plan.target_text}",
            args=args,
            expected_seconds=plan.duration,
        ),
        master_command(plan, work / "master.wav"),
    ]


def _seconds_of_sound(plan: NormalizePlan) -> float:
    """How long the finished sound is: the source's own length. One division, spelled once."""
    return float(Fraction(plan.source.duration).limit_denominator(1_000_000))


def wav_command(plan: NormalizePlan, source: Path, target: Path) -> Command:
    """The pass that decodes the normalized sound into an uncompressed WAV.

    ``source`` is the file this run has just written — the normalized master, under its temporary
    name — so the WAV is the sound the master plays rather than a second build of it.

    Three choices in the argument list are deliberate:

    * **``pcm_s16le``, 16-bit**, because that is the WAV every editor, transcription service and player
      reads without being asked which flavour of WAV it is.
    * **``-vn``**, so nothing here can produce a video stream and nothing here can touch the picture.
      Every other claim this product makes about the picture is a claim about an argument list, and
      this one is too.
    * **the plan's own length**, not the file's. The export is cut to the duration the plan states, so
      a container whose last audio packet overruns its last frame cannot put a tail of sound on an
      export that the master does not have.

    The sample rate and the channel count are *not* set: they are the finished file's, which are the
    source's — both were measured before a plan existed — and resampling here would be this feature
    inventing a number the run never chose.
    """
    return Command(
        label="write the normalized sound as a WAV",
        args=[
            tool("ffmpeg"), "-y", "-v", "error", "-i", str(source),
            "-map", "0:a:0", "-vn", "-c:a", "pcm_s16le", "-t", f"{_seconds_of_sound(plan):.6f}",
            str(target),
        ],
        expected_seconds=_seconds_of_sound(plan),
    )


def mp3_command(plan: NormalizePlan, source: Path, target: Path) -> Command:
    """The pass that encodes the MP3 — from the WAV, which is why ``source`` should be a ``.wav``.

    ``-c:a libmp3lame -b:a 320k`` is the whole of the request: 320 kbps is the format's ceiling, so the
    compressed copy is as close to the WAV beside it as the format can be, and nobody has to choose a
    bitrate. The sample rate and the channel count are once again the WAV's own.

    ``-t`` is here for the same reason it is on the WAV: the length is the plan's, so the exports, the
    master and the source all end together. It is a no-op on a WAV that was already cut to that length,
    and it is written anyway — an export whose length depends on *which* file a later edit made it read
    from is an export with two behaviours.
    """
    return Command(
        label=f"encode the normalized sound as an MP3 at {MP3_BITRATE}",
        args=[
            tool("ffmpeg"), "-y", "-v", "error", "-i", str(source),
            "-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-b:a", MP3_BITRATE,
            "-t", f"{_seconds_of_sound(plan):.6f}", str(target),
        ],
        expected_seconds=_seconds_of_sound(plan),
    )


@dataclass(frozen=True)
class SoundExport:
    """One extra sound file this run will write: where it goes, and the pass that writes it.

    ``command`` is ``None`` when ``skip`` says why it will not be written. Both fields exist on every
    export so that a caller has to look at the reason rather than at whether there is something to
    run — an export that is quietly absent is the outcome this product is worst at explaining.
    """

    path: Path
    command: Command | None
    skip: str | None = None


def sound_plan(plan: NormalizePlan) -> tuple[SoundExport, ...]:
    """The extra sound files this run writes, each with its pass or the sentence saying why not.

    ## Why an occupied name is skipped rather than refused

    This is the deliberate difference between these files and the master beside them. The master is the
    deliverable; these are copies of its sound. Refusing the whole run because a `.mp3` is already
    there would trade a normalized master for an accessory, and writing over it would break the one
    promise this product makes about every path it is handed. So the run says what it did not do, and
    the master still goes out.

    ## Why the decision is made for both files at once

    The two files are one soundtrack in two containers, so a run that wrote one of them and skipped the
    other would be a run that could not answer "where is the sound?" without a paragraph. Either both
    are written or neither is, and the sentence below names the file that is in the way.

    ## Why the decision is taken twice

    ``run_normalize`` asks this at the start — so a run that will not write them says so before the
    work — and asks the filesystem again where it acts, because these answers are minutes old by then
    and the file that appeared in between is exactly the file this product promises not to write over.

    ## What each pass is given, which is the whole of "the normalized sound"

    An export is never handed the source. The first is handed the finished file — the master itself,
    after the chain and the gain — and each later one is handed the export before it. That is how "the
    exports are the processed sound" is true without a single line of code arranging it: there is no
    unprocessed audio in reach.
    """
    if not plan.spec.audio_exports:
        return ()

    paths = plan.audio_paths
    occupied = [path for path in paths if path.exists()]
    if occupied:
        names = ", ".join(path.name for path in occupied)
        asked = ", ".join(path.name for path in paths)
        reason = (
            f"{names} is already there, and nothing here is written over: the run went ahead without "
            f"the extra sound files ({asked}). Move it if you want them from this run."
        )
        return tuple(SoundExport(path=path, command=None, skip=reason) for path in paths)

    # Built in the spec's own order, which `AUDIO_FORMATS` fixes as "the WAV, then the MP3 that is
    # encoded from it".
    #
    # `sources` is the chain each pass may read, in order: the finished master first, then everything
    # written so far. A pass takes the last entry, so the second export reads the first and a third
    # would read the second — no rule per format, and no way to express reading a source. The list can
    # never be empty at the point it is read, because the master is in it before the loop starts.
    sources: list[Path] = [plan.published_as]
    exports: list[SoundExport] = []
    for path in paths:
        builder = wav_command if path.suffix == ".wav" else mp3_command
        exports.append(SoundExport(path=path, command=builder(plan, sources[-1], path)))
        sources.append(path)
    return tuple(exports)


# ---------------------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class NormalizeResult:
    """What a run produced: where the normalized file is, and which sound files it wrote.

    ## Why the exports are reported rather than re-derived from the disk

    There are two ways a `.wav` can exist at the end of a run without this run having written it: it
    was already there when the run started (so the run skipped it), or something outside this process
    wrote it while the run was working (so the run declined to write over it). A caller that asked the
    disk "is out.wav there?" would answer yes to all three cases and offer the operator a file from an
    earlier run wearing the new master's name. So the run says what it wrote. That is the only source
    of this fact.

    It is a named type rather than a bare ``Path`` for the same reason: a caller cannot read the wrong
    field by accident, and adding to the result is a change a type checker sees.
    """

    output: Path
    audio: tuple[Path, ...] = ()
    #: The plan the run actually carried out — which for a `chain` run is **not** the plan it was
    #: given: the fader's value comes from measuring what the chain did to this file, so the plan that
    #: describes the finished file is the one that came out the other end. Reporting the input plan
    #: would be reporting a gain the run did not use.
    plan: NormalizePlan | None = None


def run_normalize(
    plan: NormalizePlan,
    *,
    cancel: CancelToken | None = None,
    log=None,
    progress=None,
    stage=None,
    heartbeat=None,
) -> NormalizeResult:
    """Carry out the plan and return what was written: the master, and which sound files.

    Everything is written inside a temporary directory beside the output, and the last command writes
    to ``<name>.part.<ext>``. The rename at the end is what makes a cancelled or failed run leave the
    destination exactly as it was rather than a truncated file wearing the deliverable's name. The
    temporary directory is beside the output rather than in the system temp so the rename is a move
    within one volume, which is the difference between an instant rename and a full copy.

    ## This function never replaces a file it did not choose

    ``output_for`` picks a name that is free, so the common case is not a refusal — pressing the button
    twice makes `2`, and the source is never in danger because the suffix guarantees the name differs
    from it. But the name is written down in the plan and the rename happens minutes later, so the
    check is taken again where it is acted on: a file that appeared at that path *during* the run is
    not replaced by this one's rename. The two paths this does write over are its own: the `.part` file
    beside the destination, which is this product's own debris from a run that was killed, and the
    directory ``mkdtemp`` makes, whose name is unique by construction.
    """
    spec = plan.spec
    # The plan's own name, extension and all — not the spec's, which carries no extension because the
    # container is not known until the file has been probed. There is one spelling of "the name this
    # run writes" and it is `plan.output`; `spec.output` is only the folder and the stem.
    output = plan.output
    # A folder where the file belongs is refused first, and it is the one case where "already exists"
    # would be the wrong sentence: a folder is not a master, and `replace()` onto one replaces nothing.
    if output.is_dir():
        raise OutputRefused(f"{output} is a folder, and a run writes a file.")
    if not output.parent.is_dir():
        raise OutputRefused(f"{output.parent} is not a folder.")
    if output.resolve() == spec.source.resolve():
        raise OutputRefused(
            f"the output would be {output}, which is the source. This product does not write over "
            f"the file it is reading."
        )

    work = Path(tempfile.mkdtemp(prefix=".normalize-", dir=str(output.parent)))
    wrote: list[Path] = []
    try:
        # ---- 1. The stage: the strategy, put through the master's own codec -----------------
        #
        # Measured through the codec rather than merely filtered, for the overshoot
        # `measured_decoded_peak_dbfs` documents: AAC's decode rings past the samples it was given, and
        # only what comes out of the decoder tells the truth about what the delivered file will peak at.
        # Every strategy goes through here, including `gain`, whose fader the plan *can* work out in
        # advance — because "the plan knows the gain" and "the file reaches the target" are different
        # claims, and on this machine's ffmpeg the second one needs the encoder measured.
        stage_file = work / f"stage{plan.container}"
        _execute(
            Command(
                label=(
                    "apply the dynamics chain" if plan.uses_chain else f"set the level to {plan.target_text}"
                ),
                args=[
                    tool("ffmpeg"), "-y", "-v", "error", "-i", str(plan.source.path),
                    "-map", "0:a:0", "-af", sound_graph(plan, "stage"),
                    "-c:a", "aac", "-b:a", plan.spec.audio_bitrate,
                    "-f", plan.muxer, str(stage_file),
                ],
                expected_seconds=plan.duration,
            ),
            cancel, log, progress, stage, heartbeat,
        )

        if stage is not None:
            stage("measuring what the chain and the encoder did to this file")
        # Two measurements of the same file: the level of the samples the fader will be applied to, and
        # the level of what the decoder hands a player. The first is reported; the second is what the
        # fader is set from.
        chain_levels = measure_levels(stage_file, timeout=_limit_for(plan))
        decoded_levels = _measure_decoded_aac(stage_file, plan)
        plan = plan.with_measurements(chain_levels.max_dbfs, decoded_levels.max_dbfs)
        if log is not None:
            left = plan.overshoot_db
            log(
                f"-> the stage put this file's peak at {_db(chain_levels.max_dbfs)} and the AAC decode "
                f"of it at {_db(decoded_levels.max_dbfs)} — {left:+.2f} dB of codec overshoot, so the "
                f"fader is {plan.total_gain_db:+.2f} dB"
            )

        # ---- 2. The fader, which is the number the request asked for -----------------------
        master = work / "master.wav"
        # `sound_graph(plan, "master")` carries the strategy's chain, the nominal gain and the residual
        # in one graph — which is why the stage pass can be measured at all and then reproduced exactly.
        fader = sound_graph(plan, "master")
        _execute(
            Command(
                label=f"set the level to {plan.target_text}",
                args=[
                    tool("ffmpeg"), "-y", "-v", "error", "-i", str(plan.source.path),
                    "-map", "0:a:0", "-af", fader,
                    "-c:a", "pcm_s24le", str(master),
                ],
                expected_seconds=plan.duration,
            ),
            cancel, log, progress, stage, heartbeat,
        )

        # ---- 3. The picture and the sound, together ----------------------------------------
        mux = master_command(plan, master)
        _execute(mux, cancel, log, progress, stage, heartbeat)

        published = plan.published_as
        if not published.is_file():
            raise FFmpegError("the run finished without writing anything", mux.args)

        # The extra sound files, in the order the spec fixed: the WAV, then the MP3 encoded from it.
        exports = sound_plan(plan)
        # One sentence for one condition: when the exports are skipped they are skipped together, for
        # the same reason, and repeating it per format would be the log saying the same thing twice.
        if exports and exports[0].skip is not None and log is not None:
            log(exports[0].skip)

        for export in exports:
            command = export.command
            # Asked again where it is acted on, for the files this run would have written. The answer
            # at the top of this function is old by now, and a file that arrived in between is exactly
            # the file this product promises not to write over.
            if command is not None and export.path.exists():
                if log is not None:
                    log(
                        f"{export.path.name} appeared while the run was working, so nothing was written "
                        f"over it, and neither sound file was written. The master itself is unaffected."
                    )
                break
            if command is None:
                continue
            _execute(command, cancel, log, progress, stage, heartbeat)
            wrote.append(export.path)

        # The rename that publishes the master, taken with the check repeated because the answer at the
        # top of this function is minutes old. A file that appeared at the destination in between is not
        # debris: it is somebody else's file, and this product does not write over it.
        if output.exists():
            raise OutputRefused(
                f"something was written to {output} while this run was working, so the finished file "
                f"was not put there. It is at {published} and nothing was lost."
            )
        shutil.move(str(published), str(output))
    except BaseException:
        # The temporary directory is this product's own, and a run that failed has nothing worth
        # keeping in it. The master under its temporary name goes with it, so a failure cannot leave a
        # complete-looking file at a path the operator was never told about.
        shutil.rmtree(work, ignore_errors=True)
        try:
            plan.published_as.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    shutil.rmtree(work, ignore_errors=True)

    # The exports are only reported once the master is in place: an export that was written and then a
    # rename that failed would leave a `.wav` beside a file that does not exist, and reporting it as
    # this run's deliverable would be the report lying about the run.
    if wrote and cancel is not None and cancel.cancelled():
        raise Cancelled("the run was cancelled")
    return NormalizeResult(output=output, audio=tuple(wrote), plan=plan)


def _db(value: float | None) -> str:
    """A level as the engine's own sentence. `None` is digital silence and says so."""
    return "silence" if value is None else f"{value:.2f} dBFS"


def _measure_decoded_aac(stage_file: Path, plan: NormalizePlan):
    """What the decoder hands a player, measured by decoding the stage file to PCM and reading it.

    ## Why not measure the encoded file directly

    ``volumedetect`` reports the level of *decoded* samples, so pointing it at an `.m4a` would already
    give the right answer — and it was tried first, and it is not reliable here: the file is being
    written into a temporary directory with a container extension, and a measurement whose correctness
    depends on which muxer ffmpeg picked for a path nobody will ever see again is a measurement with a
    second failure mode hidden in it.

    So the decode is explicit: the sound is extracted to a WAV at the source's own rate and channel
    count — the same rate and count the fader will write at — and *that* is what is measured. It also
    makes the figure auditable: the WAV is a file anybody can run `volumedetect` over by hand and get
    the same number.

    ## Why it costs a pass and is worth it

    This is the difference between a plan that promises a level and a run that reaches one. Measured on
    this machine: a 1 kHz tone written at −6.00 dBFS in PCM comes out of an AAC decode at −5.40, and no
    limiter upstream can prevent it because the ringing happens after the samples. Without this pass the
    product would be out by its codec's overshoot on every file while reporting a number the plan
    computed correctly.
    """
    audio = plan.source.audio
    assert audio is not None  # plan_normalize refuses a source with no sound
    decoded = stage_file.with_name(f"{stage_file.stem}-decoded.wav")
    args = [
        tool("ffmpeg"), "-y", "-v", "error", "-i", str(stage_file),
        "-map", "0:a:0", "-vn", "-c:a", "pcm_s24le",
        "-ar", str(audio.sample_rate), "-ac", str(audio.channels), str(decoded),
    ]
    code, _, err = capture(args, timeout=_limit_for(plan))
    if code != 0:
        raise FFmpegError("the stage file could not be decoded back for measurement", args, err)
    return measure_levels(decoded, timeout=_limit_for(plan))


def _limit_for(plan: NormalizePlan) -> float:
    """How long a measurement of this file may take.

    The same loose derivation `process.run` uses for a pass: ten times the material's length plus a
    minute, and never less than three minutes. A measurement is a full decode, so it is bounded by the
    same thing a decode is.
    """
    return max(180.0, plan.duration * 10.0 + 60.0)


def _execute(command: Command, cancel, log, progress, stage, heartbeat) -> None:
    """Run one command of the plan, reporting the stage it is."""
    if stage is not None:
        stage(command.label)
    run(
        command.args,
        cancel=cancel,
        log=log,
        progress=progress,
        heartbeat=heartbeat,
        expected_seconds=command.expected_seconds,
    )
