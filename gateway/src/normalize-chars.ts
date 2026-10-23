// Invisible format characters that models ignore: soft hyphen (0xAD), zero-width space, joiners and direction marks
// (0x200B-0x200F), word joiner and invisible operators (0x2060-0x2064), byte-order mark (0xFEFF).
// Built from code points so the source stays plain ASCII and reviewers can see exactly what is stripped.
const cp = (n: number) => String.fromCodePoint(n);
export const INVISIBLE = new RegExp(`[${cp(0xad)}${cp(0x200b)}-${cp(0x200f)}${cp(0x2060)}-${cp(0x2064)}${cp(0xfeff)}]`);
