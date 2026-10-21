/**
 * Incremental scanner for streamed model output.
 *
 * Text is released only once it can no longer be part of a match that is still arriving: the last
 * `holdback` characters stay buffered until more text comes or the stream ends. So a card number split
 * across two SSE chunks ("4111 1111" + " 1111 1111") is still caught before any of it is sent.
 */
import { classify, redact, type DataClass } from "./classify.js";

export type ScanVerdict =
  | { kind: "emit"; text: string; found: DataClass[] }
  | { kind: "abort"; cls: DataClass };

export class StreamScanner {
  private buffer = "";
  readonly seen = new Set<DataClass>();

  constructor(
    private readonly holdback: number,
    private readonly redactClasses: ReadonlySet<string>,
    private readonly blockClasses: ReadonlySet<string>,
  ) {}

  push(chunk: string): ScanVerdict {
    this.buffer += chunk;
    return this.release(false);
  }

  end(): ScanVerdict {
    return this.release(true);
  }

  private release(final: boolean): ScanVerdict {
    const { spans } = classify(this.buffer);
    for (const s of spans) {
      this.seen.add(s.cls);
      if (this.blockClasses.has(s.cls)) return { kind: "abort", cls: s.cls };
    }
    // Never cut through a detected span: release up to the safe point, keep the tail buffered.
    let safe = final ? this.buffer.length : Math.max(0, this.buffer.length - this.holdback);
    for (const s of spans) if (s.start < safe && s.end > safe) safe = s.start;
    const releasedSpans = spans.filter((s) => s.end <= safe);
    const text = redact(this.buffer.slice(0, safe), releasedSpans, this.redactClasses);
    this.buffer = this.buffer.slice(safe);
    return { kind: "emit", text, found: [...new Set(releasedSpans.map((s) => s.cls))] };
  }
}
