/**
 * The window's arithmetic and formatting.
 *
 * What is worth testing without a browser is exactly this: a level written as a sentence, a byte count as
 * a magnitude, a field parsed as the number of decibels it means. Everything else in the interface is a
 * layout question, and the way to check a layout is to look at the running window — which is what
 * `scripts/check_window.py` does, in a real one.
 *
 * The rule these tests defend is the product's R20: **no figure the engine could have measured is
 * computed in the interface.** So nothing here derives a gain or a duration. What is here turns a number
 * the engine sent into the string a person reads, and turns a string a person typed into the number that
 * goes back.
 */

import { describe, expect, it } from "vitest";

import { bytes, clock, decibels, fieldValue, folderOf, level, nameOf, signed, stemOf } from "./format";

describe("clock", () => {
  it("writes a duration as H:MM:SS.mmm, with the hours not padded", () => {
    expect(clock(0)).toBe("0:00:00.000");
    expect(clock(11)).toBe("0:00:11.000");
    expect(clock(61.5)).toBe("0:01:01.500");
    expect(clock(3661.25)).toBe("1:01:01.250");
  });

  it("never writes a negative clock, because a negative elapsed time is not a state", () => {
    expect(clock(-5)).toBe("0:00:00.000");
  });
});

describe("bytes", () => {
  it("is decimal, because a file size is counted in decimal by everything that opens the file", () => {
    expect(bytes(999)).toBe("999 B");
    expect(bytes(1000)).toBe("1.00 kB");
    expect(bytes(190_000_000)).toBe("190 MB");
    expect(bytes(1_500_000_000)).toBe("1.50 GB");
  });

  it("gives a magnitude's precision, not a precision it does not have", () => {
    // `190.0 MB` is a size estimate pretending to four significant figures.
    expect(bytes(190_400_000)).toBe("190 MB");
    expect(bytes(19_400_000)).toBe("19.4 MB");
    expect(bytes(1_940_000)).toBe("1.94 MB");
  });

  it("says zero for a file nothing has measured yet rather than NaN", () => {
    expect(bytes(0)).toBe("0 B");
    expect(bytes(Number.NaN)).toBe("0 B");
    expect(bytes(-1)).toBe("0 B");
  });
});

describe("decibels", () => {
  it("reads a field the way the field is written", () => {
    expect(decibels("-6.0")).toBe(-6);
    expect(decibels("12")).toBe(12);
    expect(decibels("  -3.5  ")).toBe(-3.5);
    expect(decibels("+2")).toBe(2);
  });

  it("is null for a field with no number in it, which is not the same as zero", () => {
    // The distinction matters: `Number("")` is `0`, and an emptied target field would silently ask for a
    // target of zero dBFS — a file that clips by construction.
    expect(decibels("")).toBeNull();
    expect(decibels("   ")).toBeNull();
    expect(decibels("loud")).toBeNull();
    expect(decibels("-")).toBeNull();
    expect(decibels("")).not.toBe(0);
  });
});

describe("fieldValue", () => {
  it("writes a number the way the field shows it", () => {
    expect(fieldValue(-6)).toBe("-6.0");
    expect(fieldValue(12)).toBe("12.0");
  });
});

describe("level", () => {
  it("writes a measured peak with its unit", () => {
    expect(level(-6)).toBe("-6.0 dBFS");
    expect(level(-27.14)).toBe("-27.1 dBFS");
  });

  it("says silence rather than a very negative number", () => {
    // Digital silence has no level. A number would be arithmetic waiting to happen on a quantity that is
    // not there — and the engine's own sentence for it is the word.
    expect(level(null)).toBe("silence");
  });
});

describe("signed", () => {
  it("always shows the sign, because a level that moved has a direction", () => {
    expect(signed(0.3)).toBe("+0.30");
    expect(signed(-0.3)).toBe("-0.30");
    expect(signed(0)).toBe("+0.00");
  });
});

describe("paths", () => {
  it("finds the folder, with either separator and never a trailing one", () => {
    expect(folderOf("C:\\media\\talk.mp4")).toBe("C:\\media");
    expect(folderOf("/media/talk.mp4")).toBe("/media");
    expect(folderOf("talk.mp4")).toBe("");
  });

  it("finds the name, whichever separator the path uses", () => {
    expect(nameOf("C:\\media\\talk.mp4")).toBe("talk.mp4");
    expect(nameOf("talk.mp4")).toBe("talk.mp4");
  });

  it("finds the stem without eating a dot that is part of the name", () => {
    // `talk.en.mp4` is a file called `talk.en`, and this is only ever used for a label — the engine
    // decides what the output is called.
    expect(stemOf("C:\\media\\talk.en.mp4")).toBe("talk.en");
    expect(stemOf("C:\\media\\talk")).toBe("talk");
  });
});
