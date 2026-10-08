# Manual tests

Everything a machine cannot check. `backend/tests` launches no ffmpeg, `scripts/check_normalize.py` drives
the engine without a window, and `scripts/check_window.py` drives the page without a shell — so what is left
is the person in front of the window, and this is the list.

Run it once per machine the product is deployed to. Each line says what to do, what should happen, and —
where it matters — what it would mean if it did not.

---

## 1. First run on a machine with nothing installed

| Step | Expected |
|---|---|
| Double-click `start.bat` on a PC with no Python, no Node, no ffmpeg | a console that names each thing it is fetching, then a window. No administrator prompt at any point |
| Watch for a black rectangle flashing | nothing flashes. Every tool is spawned with `CREATE_NO_WINDOW` |
| Close the window | the console reports the engine stopped, and the process list has no `ffmpeg` and no `python` left behind |
| Double-click `start.bat` again | it goes straight to the window; nothing is downloaded a second time |

**If the window does not open**, the page that appears instead says which interpreter was tried, on which
port, and the last thing the engine printed. A white screen with no text is a defect worth reporting.

## 2. A batch of real files

Prepare an episode's worth: at least one 1080p video with a soundtrack, one file quieter than the target and
one louder, and one sound-only file.

| Step | Expected |
|---|---|
| `Add files…` and select all of them at once | the dialog takes several; each row appears and goes to *Ready* with a measured level |
| Read the *Measured* column | every row has a level. A row that says `…` forever means a `volumedetect` pass is still running, and a row that says `silence` for a file you can hear is a defect |
| Add a file twice | two rows, and two different destinations. The second run does **not** overwrite the first |
| Press **Normalize** | one file at a time, in queue order. The row's state moves *Working* → *Done* and the console names the pass it is on |
| Watch the bar while a pass runs | it moves. A bar that sits still for a whole file means `fraction` is `null`, which is honest for a measurement pass and wrong for an encode |
| Press **Esc** mid-run | the file being worked on stops, the rows behind it are reported as stopped, and **no partial file is left at any destination** |
| Check the source files afterwards | unchanged. Compare a modification time if in doubt |

## 3. The picture really is untouched

| Step | Expected |
|---|---|
| Select a finished video and look at *The file* | the *Picture* row reads **copied, not re-encoded**, and the *picture copied* check reads **passed** with a count of sampled frames |
| Play the original and the normalized copy side by side on a colour-critical frame | identical. The sound is at a different level; the picture is not |
| Take a video whose container cannot hold AAC (a `.webm`) | the plan carries a caution naming the container it will be written as, and the picture check still passes |

## 4. The level

| Step | Expected |
|---|---|
| Set the target to −6.0 and run a file | *Target level* reads **passed** with a figure within about a decibel of −6.0 |
| Set the target to −1.0 on the default strategy | it reaches −1.0. If it does not, the strategy is the operator's chain and the window should have said so |
| Choose the operator's chain and set the target to −1.0 | the prose under the strategy is drawn as a caution, and the plan carries one naming the level the chain can reach |
| Run a file whose sound is digital silence | no gain is applied, the plan says so, and the check reads **passed** rather than failing |
| Look at *Off target by* after a run | a small signed figure. A figure above about a decibel on ordinary material is worth reporting with the file |

## 5. The sound you can hear

This is the one check nothing automated can make, and it is the reason to do a run by ear at least once.

| Step | Expected |
|---|---|
| Listen to the seam of a file that was loud and is now quieter | no click, no gap, no change of speed. A level change is smooth from the first sample |
| Listen to the first and last second | the sound starts and ends where the original does. A tail of silence, or a missing word, is a defect |
| With the operator's chain: listen for pumping | the chain is a limiter doing all the work, so material that hits it will sound flatter than the source. That is what the setting does; what would be wrong is *distortion*, which is what a target above the limiter's working level would produce |
| With **Gain, then a limiter** on a very quiet file | it is lifted without clipping. The loudest moments should sound compressed rather than broken |

## 6. The sound files

| Step | Expected |
|---|---|
| Tick **WAV** and **MP3**, run | three files beside each other, one name apart |
| Click **Show .wav** and **Show .mp3** in the report | Explorer opens with the file selected. It never *runs* the file |
| Open the WAV in an editor | it is the normalized sound, at the master's level — not the source's |
| Play the MP3 | the same sound as the WAV, at 320 kbps |
| Run the same file again to the same destination | the master is written under a stepped-aside name and the exports are **skipped with a sentence** naming the file in the way. The master still goes out |
| Check the report's reveal buttons after a skipped export | they are not offered, because the run did not write them |

## 7. Refusals

| Step | Expected |
|---|---|
| Add a file that is not media (a `.txt`) | its row says *Failed* and the sentence explains that it could not be read. The rest of the queue is unaffected |
| Add a file that has a picture and no sound | refused with the reason that there is nothing to normalize |
| Type `6` into the target field | refused, with the range it works in. The field keeps what you typed and the field itself is marked |
| Open a destination that is a folder | refused with the sentence that says a run writes a file |
| Disconnect the network and run | everything works. Nothing here talks to a network |

## 8. The window itself

| Step | Expected |
|---|---|
| Maximize, and restore to the smallest size the window will go to | nothing scrolls except the queue, the log and the report. No control is cut off and none overlaps another |
| Tab through the settings | every control takes focus, the focus ring is visible on each, and the labels are read by a screen reader (see §9) |
| Press `F5`, `Esc`, `Del`, `Ctrl+O` | start, stop, remove the selected row, add files |
| Look at the footer | the ffmpeg lamp is green, and the queue's counts and total size are there |
| Close the window during a run | the engine stops with it. Check the process list: no `ffmpeg`, no `python` |

## 9. Accessibility, once per machine

| Step | Expected |
|---|---|
| Run Windows Narrator or NVDA and tab through the settings | each field is announced with its label — *Target peak level in dBFS*, *Make-up gain in decibels* — and each checkbox with its word |
| Tab to the queue | the table announces its caption, its column headings and each row's file and state |
| Look at a failed row's pill | it carries the **word**, not only the colour. Roughly one man in twelve cannot separate the red from the green, and a tool where "failed" is only a colour lies to him |
| Turn on Windows high contrast | controls keep an edge, the selection stays visible, and the select's indicator does not disappear |
| Turn on *Reduce motion* | the indeterminate progress sweep stops moving and becomes a full-width fill, so "working" still does not look like "not started" |

## 10. What this checklist does not cover

- **A 17-minute episode.** The reference measurement was an 11-second clip. How long the picture copy takes
  on a long file, what the AAC encode of it costs, and whether a source with several video streams maps the
  way it should are all unmeasured. Run one and watch the console's own timings.
- **A file with subtitles, chapters or several audio tracks.** The plan names the streams it will not carry
  over; whether that list is right for your material is worth reading once.
- **A target below −9 dBFS through the operator's chain.** The chain's fader can attenuate, but the limiter
  is still in the path and nothing has been listened to there.
