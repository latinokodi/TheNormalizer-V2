/**
 * The empty queue: the one screen that has to teach the operator what to do.
 *
 * ## Why this is its own component
 *
 * It was three paragraphs of inline JSX inside `QueueTable`, which made it hard to give the attention it
 * needs: an empty screen is the only screen a new operator sees, and a new operator has exactly one question
 * — *how do I put a file in*. Three paragraphs answering that in prose is a screen that answers it in the
 * place least likely to be read.
 *
 * So the answer is a **button**, at the size of a button, with the dialog's own verb on it (`Add files`), a
 * line under it listing every way in, and a dashed outline around the whole area that says *files go here*.
 * The outline is not decoration: it is the shape of a drop target, which is a thing every operator has used
 * before, and it is the affordance the prose was trying to describe.
 *
 * ## The drop zone, and the promise this file used to make without keeping
 *
 * The screen used to say *"drop a folder's worth in at once"* and **nothing in the program handled a drop**.
 * A promise in the copy with no implementation behind it is worse than no promise: it sends somebody to try
 * a thing that cannot work, and what they conclude is that the program is broken rather than that the
 * sentence was.
 *
 * The drop is handled now — the `onDrop`/`onDragOver` pair below reads the paths out of the dropped files
 * through the bridge's `pathForDroppedFile`, which is `webUtils.getPathForFile` in the main world — and the
 * zone only highlights when a drag is actually over it.
 */

import type { QueueRow } from "../state/queue";
import { bytes } from "../lib/format";

interface Props {
  readonly rows: readonly QueueRow[];
  readonly measuring: boolean;
  readonly onAdd: () => void;
  readonly onDropPaths: (paths: readonly string[]) => void;
}

/** How many files are being read right now, and how big they are. The figures an operator waits on. */
function progress(rows: readonly QueueRow[]): string {
  const reading = rows.filter((row) => row.stage === "reading").length;
  const total = rows.reduce((sum, row) => sum + (row.media?.sizeBytes ?? 0), 0);
  const parts: string[] = [];
  if (reading > 0) {
    parts.push(`Reading ${reading} file${reading === 1 ? "" : "s"}…`);
  }
  if (total > 0) {
    parts.push(`${bytes(total)} in total`);
  }
  return parts.join(" · ");
}

export function EmptyQueue({ rows, measuring, onAdd, onDropPaths }: Props) {
  const note = progress(rows);

  return (
    <div
      className="empty-state empty-state--drop"
      /*
       * The whole area is the drop target. `onDragOver` has to `preventDefault()` or the browser refuses
       * the drop and the cursor shows a "no" — which is the difference between a zone that looks like one
       * and a zone that is one.
       */
      onDragOver={(event) => {
        event.preventDefault();
      }}
      onDrop={(event) => {
        event.preventDefault();
        onDropPaths(Array.from(event.dataTransfer.files).map((file) => String(file)));
      }}
    >
      <div className="empty-state__icon" aria-hidden="true">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="17 8 12 3 7 8" />
          <line x1="12" y1="3" x2="12" y2="15" />
        </svg>
      </div>

      <button type="button" className="btn btn--primary btn--big empty-state__add" onClick={onAdd}>
        Add files
      </button>

      <p className="empty-state__how">
        Choose them with the operating system's own dialog — several at once, video or audio, a folder's
        worth. The same button is in the title bar, and <strong>F5</strong> starts a run.
      </p>

      <div className="empty-state__tags">
        <span className="pill">MP4</span>
        <span className="pill">MKV</span>
        <span className="pill">MOV</span>
        <span className="pill">WAV</span>
        <span className="pill">MP3</span>
        <span className="pill">FLAC</span>
      </div>

      {note === "" ? null : <p className="empty-state__progress">{note}</p>}

      <div className="empty-state__body">
        <p>
          <strong>Nothing queued.</strong> This takes video and audio files to a peak level you choose, and
          evens out the voices so a quiet speaker sits beside a loud one.
        </p>
        <p>
          The picture is stream-copied, so a video costs one audio encode rather than a re-encode, and only
          the sound is rewritten. Each normalized copy is written beside its source with{" "}
          <span className="figures"> - normalized</span> in the name — your originals are never touched.
        </p>
      </div>
    </div>
  );
}
