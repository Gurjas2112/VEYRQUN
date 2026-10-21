import { describe, expect, it } from "vitest";
import { classify, ibanValid, luhnValid, maskPan, redact, verhoeffValid } from "../src/classify.js";
import { costMinor, estimateTokens } from "../src/pricing.js";
import { decimalToMinor, minorToDecimalString } from "../src/pipeline.js";
import { StreamScanner } from "../src/stream-scan.js";
import { counterKeys, localDate } from "../src/counters.js";

describe("checksums", () => {
  it("Luhn", () => {
    expect(luhnValid("4111111111111111")).toBe(true);
    expect(luhnValid("4111111111111112")).toBe(false);
  });
  it("Verhoeff (Aadhaar)", () => {
    expect(verhoeffValid("234123412346")).toBe(true);
    expect(verhoeffValid("234123412347")).toBe(false);
  });
  it("IBAN mod-97", () => {
    expect(ibanValid("GB82 WEST 1234 5698 7654 32")).toBe(true);
    expect(ibanValid("GB82 WEST 1234 5698 7654 33")).toBe(false);
  });
});

describe("classify", () => {
  it.each([
    ["mail me at jane.doe@example.com", "email"],
    ["card 4111 1111 1111 1111 please", "pci_pan"],
    ["aadhaar 2341 2341 2346", "aadhaar"],
    ["PAN ABCPE1234F on file", "pan_in"],
    ["IBAN GB82WEST12345698765432", "iban"],
    ["call +91 98765 43210", "phone"],
  ])("%s -> %s", (text, cls) => {
    expect(classify(text).classes).toContain(cls);
  });
  it("does not flag Luhn-invalid digit runs or plain numbers", () => {
    expect(classify("order 4111111111111112 and 123456").classes).toEqual([]);
  });
  it("redacts only the requested classes", () => {
    const t = "email jane@x.io card 4111111111111111";
    const { spans } = classify(t);
    expect(redact(t, spans, new Set(["email"]))).toBe("email [REDACTED:email] card 4111111111111111");
  });
  it("masks PANs to first 6 / last 4", () => {
    expect(maskPan("4111 1111 1111 1111")).toBe("411111******1111");
  });
});

describe("stream scanner", () => {
  it("catches a card number split across chunks before releasing any of it", () => {
    const s = new StreamScanner(64, new Set(), new Set(["pci_pan"]));
    const parts = ["Sure, the card is 4111 11", "11 1111 ", "1111 thanks"];
    const out: string[] = [];
    for (const p of parts) {
      const v = s.push(p);
      if (v.kind === "abort") { out.push("<ABORT>"); break; }
      out.push(v.text);
    }
    expect(out.join("")).not.toMatch(/4111/);
    expect(out.at(-1)).toBe("<ABORT>");
  });
  it("redacts an email split across chunks", () => {
    const s = new StreamScanner(64, new Set(["email"]), new Set());
    let text = "";
    for (const p of ["reach jane.d", "oe@exam", "ple.com today"]) {
      const v = s.push(p);
      if (v.kind === "emit") text += v.text;
    }
    const v = s.end();
    if (v.kind === "emit") text += v.text;
    expect(text).toBe("reach [REDACTED:email] today");
  });
  it("releases text promptly once it can't be part of a match", () => {
    const s = new StreamScanner(32, new Set(), new Set());
    const v = s.push("x".repeat(100));
    expect(v.kind === "emit" && v.text.length).toBe(68);
  });
});

describe("money and counters", () => {
  it("parses decimals exactly and rejects extra precision", () => {
    expect(decimalToMinor("600.00", "USD")).toBe(60000);
    expect(decimalToMinor("0.1", "USD")).toBe(10);
    expect(decimalToMinor("1.005", "USD")).toBeNull();
    expect(decimalToMinor("-1", "USD")).toBeNull();
    expect(decimalToMinor("12", "JPY")).toBe(12);
    expect(minorToDecimalString(60000, "USD")).toBe("600.00");
    expect(minorToDecimalString(5, "USD")).toBe("0.05");
  });
  it("LLM cost rounds up to a whole minor unit", () => {
    expect(costMinor({ input_per_mtok: 0.5, output_per_mtok: 1.5 }, 100, 100)).toBe(1);
    expect(costMinor({ input_per_mtok: 0, output_per_mtok: 0 }, 100, 100)).toBe(0);
    expect(estimateTokens("abcd".repeat(10))).toBe(14);
  });
  it("counter keys use the agent's local date and a cluster hash tag", () => {
    const at = new Date("2026-10-21T20:00:00Z");
    expect(localDate("Asia/Kolkata", at)).toBe("2026-10-22");
    expect(counterKeys("ws", "a", "pay", "UTC", at)[0]).toBe("vq:{ws:a}:spend:2026-10-21");
  });
});
