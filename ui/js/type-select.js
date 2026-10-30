/**
 * type-select.js — "type to select" for the file list (like Windows Explorer,
 * but it also finds words in the middle of a name).
 *
 *   TypeSelect.find(names, typed, from, fresh) → index | -1
 *
 * Best kind of match wins, then the first one at/after *from* (wrapping):
 *   0  the name starts with what was typed          "15" → "15. SHILA …"
 *   1  …once leading numbering is ignored            "shila" → "15. SHILA …"
 *   2  a word in the name starts with it              "mahmud", "md shahed"
 *   3  it appears anywhere in the name                "hajab" → "MAHAJABIN"
 * *fresh* (a new search / the same letter again) starts after *from*, so
 * pressing one letter repeatedly cycles through the matches; while typing
 * more letters the current item is kept if it still matches.
 */
const TypeSelect = (() => {
  const norm = s => String(s || '').normalize('NFC').toLocaleLowerCase().replace(/\s+/g, ' ');
  // "15. ", "(2) ", "03 - ", "#4_" … at the start of a name
  const LEAD = /^[\s(\[#]*\d+[\s.)\]:_-]*/u;
  const WORD_START = /(^|[^\p{L}\p{N}\p{M}])(?=[\p{L}\p{N}])/gu;

  function rank(name, typed) {
    const n = norm(name), t = norm(typed);
    if (!t.trim()) return -1;
    if (n.startsWith(t)) return 0;
    const rest = n.replace(LEAD, '');
    if (rest !== n && rest.startsWith(t)) return 1;
    WORD_START.lastIndex = 0;
    let m;
    while ((m = WORD_START.exec(n)) !== null) {
      const at = m.index + m[1].length;
      if (n.startsWith(t, at)) return 2;
      if (WORD_START.lastIndex === m.index) WORD_START.lastIndex++;
    }
    return n.includes(t) ? 3 : -1;
  }

  function find(names, typed, from = -1, fresh = true) {
    const len = names.length;
    if (!len || !norm(typed).trim()) return -1;
    let best = 4, pick = -1;
    const start = fresh ? from + 1 : Math.max(0, from);
    for (let k = 0; k < len; k++) {
      const i = (((start + k) % len) + len) % len;
      const r = rank(names[i], typed);
      if (r >= 0 && r < best) { best = r; pick = i; if (r === 0) break; }
    }
    return pick;
  }

  /** True when the buffer is one letter pressed again and again ("sss"). */
  const repeated = s => s.length > 1 && [...s].every(c => c === s[0]);

  return { find, rank, repeated };
})();

if (typeof module !== 'undefined') module.exports = TypeSelect;
