// 查經 PPT builder (WKP-F11.07). Moved from the 2026-09-25 session generator;
// every rule in section 5.1 of
// docs/wang-knowledge-platform/60-reference-commentary/bible_study_prep_solution_v1.md
// lives here. The owner's four requirements:
//   1. fonts large and clear            -> PT below, fit-checked at fixed sizes
//   2. quoted scripture shown in full   -> verbatim 和合本, split by verse, never shrunk
//   3. every point of the script        -> every script paragraph claimed by a slide's notes
//   4. slides show key points only      -> at most 4 bullets, each on one line
// Each build also writes 「…（投影片對照）.md」: the script with slide numbers.
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");
const { chapterVerses } = require("./cuv");

const W = 13.333, H = 7.5, MX = 0.6;
const BODY_TOP = 1.45, BODY_H = 5.6;
const FONT = "Microsoft JhengHei";
const C = { primary: "6D2E46", accent: "D4A24C", text: "222222", muted: "5A5A5A", card: "F4ECEF", white: "FFFFFF", line: "D9C7CF", callout: "FBF3E4" };
const PT = { title: 40, body: 32, sub: 28, verse: 32, inlineVerse: 28, table: 26, statement: 40, closing: 30 };
const { BOOK, SHORT } = require("./books");
const MAX_POINTS = 4;

// ---------- measuring (calibrated against PowerPoint's Microsoft JhengHei, slack kept) ----------
function em(ch) { const c = ch.codePointAt(0); if (c < 0x2e80) return ch === " " ? 0.3 : 0.58; return 0.97; }
function widthIn(s, pt) { let w = 0; for (const ch of s) w += em(ch); return (w * pt) / 72; }
// measured 2026-09-24: ~27 CJK chars per 11.5in line at 32pt; line pitch ~1.2x the point size
function lines(s, pt, boxW) { return Math.max(1, Math.ceil(widthIn(s, pt) / (boxW * 0.985))); }
const lineH = (pt, mult = 1.15) => (pt * 1.16 * mult) / 72;

// ---------- scripture ----------
function parseSeg(seg) {
  const m = seg.match(/^(\w+) (\d+):(\d+)(?:-(\d+))?$/);
  if (!m) throw new Error("bad ref " + seg);
  const [, book, ch, a, b] = m;
  return { book, ch: +ch, from: +a, to: +(b || a) };
}
function verses(segs) {
  const out = [];
  for (const s of segs.map(parseSeg)) {
    const chap = chapterVerses(s.book, s.ch);
    for (let v = s.from; v <= s.to; v++) {
      if (!chap[v]) throw new Error(`missing ${s.book} ${s.ch}:${v}`);
      out.push({ book: s.book, ch: s.ch, v, text: chap[v], opens: v === s.from });
    }
  }
  return out;
}
// Does `v` start a separate passage in another chapter, not the next verse of
// the reading? 16:21 then 17:22 does; 19:30 then 20:1 (the reading running on
// into the next chapter) does not.
function newPassage(prev, v) {
  if (!prev || !v.opens || prev.book !== v.book || prev.ch === v.ch) return false;
  const last = Math.max(...Object.keys(chapterVerses(prev.book, prev.ch)).map(Number));
  return !(v.ch === prev.ch + 1 && v.v === 1 && prev.v === last);
}
function label(segs) {
  const ps = segs.map(parseSeg);
  const book = BOOK[ps[0].book];
  const same = ps.every((p) => p.book === ps[0].book);
  const one = (p) => (p.from === p.to ? `${p.ch}:${p.from}` : `${p.ch}:${p.from}–${p.to}`);
  const lastVerse = (p) => Math.max(...Object.keys(chapterVerses(p.book, p.ch)).map(Number));
  if (same && ps.length === 2 && ps[1].from === 1 && ps[0].ch + 1 === ps[1].ch && ps[0].to === lastVerse(ps[0])) return `${book} ${ps[0].ch}:${ps[0].from}–${ps[1].ch}:${ps[1].to}`;
  if (same) return `${book} ${ps.map(one).join("；")}`;
  return ps.map((p) => `${BOOK[p.book]} ${one(p)}`).join("；");
}

class Deck {
  constructor(scriptPath, meta) {
    this.scriptPath = scriptPath;
    this.meta = meta;
    this.paras = fs.readFileSync(scriptPath, "utf8").split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean);
    this.claimed = new Set();
    this.slideOf = {};
    this.pending = [];
    this.sectionLabel = "";
    this.nextSection = null;
    this.pres = new pptxgen();
    this.pres.layout = "LAYOUT_WIDE";
    this.pres.title = meta.title;
    this.requiredVerses = [];
    this.slideVerses = {}; // slide number -> [{ref, vs}] shown on it, for the cross-reference
    this.count = 0;
  }
  paraText(i) {
    let t = this.paras[i];
    if (t === undefined) throw new Error("no paragraph " + i);
    t = t.replace(/\s*<!--[\s\S]*?-->/g, ""); // the checker's note tags, not for the speaker
    if (t.startsWith("|")) {
      t = t.split("\n").filter((r) => !/^\|\s*-/.test(r)).map((r) => r.replace(/^\||\|$/g, "").split("|").map((c) => c.trim()).join("｜")).join("\n");
    }
    return t.replace(/\*\*/g, "").replace(/^#+\s*/gm, "").replace(/^>\s*/gm, "").replace(/^- /gm, "・");
  }
  // claim paragraphs for the current slide (plus any held by a section marker)
  notesFor(paras, extra) {
    const all = [...this.pending, ...(paras || [])];
    this.pending = [];
    all.forEach((i) => { this.claimed.add(i); if (!(i in this.slideOf)) this.slideOf[i] = this.count; });
    const parts = all.map((i) => this.paraText(i));
    if (extra) parts.push(extra);
    return parts.join("\n\n");
  }
  newSlide(bg = C.white) {
    const s = this.pres.addSlide();
    s.background = { color: bg };
    this.count++;
    const light = bg === C.white;
    s.addText(String(this.count), { x: W - 1.1, y: H - 0.5, w: 0.7, h: 0.35, fontFace: FONT, fontSize: 14, color: light ? "9A9A9A" : "E8D5DD", align: "right", margin: 0, isTextBox: true });
    if (light && this.sectionLabel) {
      s.addText(this.sectionLabel, { x: MX, y: H - 0.5, w: 8, h: 0.35, fontFace: FONT, fontSize: 14, color: "9A9A9A", margin: 0, isTextBox: true });
    }
    return s;
  }
  titleText(s, text) {
    if (widthIn(text, PT.title) > W - 2 * MX) throw new Error("title too long: " + text);
    s.addText(text, { x: MX, y: 0.4, w: W - 2 * MX, h: 0.85, fontFace: FONT, fontSize: PT.title, bold: true, color: C.primary, margin: 0, valign: "middle", isTextBox: true });
  }
  // A section no longer gets its own slide: its name is shown small above the
  // title of its first slide and at the foot of every slide in it.
  section({ num, title, paras }) {
    this.sectionLabel = `${num}、${title}`;
    this.nextSection = `${num}、${title}`;
    this.pending.push(...(paras || []));
  }
  sectionChip(s) {
    if (!this.nextSection) return;
    s.addText(this.nextSection, { x: MX, y: 0.06, w: 9, h: 0.36, fontFace: FONT, fontSize: 20, bold: true, color: C.accent, margin: 0, isTextBox: true });
    this.nextSection = null;
  }

  // ---- building blocks ----
  bulletRuns(items, pt) {
    const runs = [];
    items.forEach((it) => {
      const o = typeof it === "string" ? { t: it } : it;
      runs.push({ text: o.t, options: { bullet: { indent: 28 }, fontSize: pt, bold: !!o.em, color: o.em ? C.primary : C.text, paraSpaceAfter: 10, breakLine: true } });
      (o.sub || []).forEach((st) => runs.push({ text: st, options: { bullet: { indent: 28 }, indentLevel: 1, fontSize: PT.sub, color: C.muted, paraSpaceAfter: 8, breakLine: true } }));
    });
    delete runs[runs.length - 1].options.breakLine;
    return runs;
  }
  bulletsHeight(items, pt, boxW) {
    let h = 0;
    for (const it of items) {
      const o = typeof it === "string" ? { t: it } : it;
      h += lines(o.t, pt, boxW - 0.45) * lineH(pt) + 10 / 72;
      for (const st of o.sub || []) h += lines(st, PT.sub, boxW - 0.95) * lineH(PT.sub) + 8 / 72;
    }
    return h;
  }
  // the number shown before a verse: book when a card mixes books, chapter when it changes
  // A card holding passages from different chapters (太 16:21；17:22–23) labels
  // each passage with its chapter, the first one too (the owner's edit of 2026-09-25).
  verseNum(vs, i) {
    const v = vs[i], prev = vs[i - 1];
    const multiBook = new Set(vs.map((x) => x.book)).size > 1;
    if (multiBook) return !prev || prev.book !== v.book ? `${SHORT[v.book]} ${v.ch}:${v.v}` : prev.ch !== v.ch ? `${v.ch}:${v.v}` : `${v.v}`;
    const passages = vs.some((x, j) => newPassage(vs[j - 1], x));
    return (passages && i === 0) || (prev && prev.ch !== v.ch) ? `${v.ch}:${v.v}` : `${v.v}`;
  }
  verseRuns(vs, pt) {
    const runs = [];
    vs.forEach((v, i) => {
      // and a blank line between the passages
      if (newPassage(vs[i - 1], v)) runs.push({ text: "", options: { fontSize: pt, breakLine: true } });
      const num = this.verseNum(vs, i);
      runs.push({ text: `${num}  `, options: { fontSize: Math.round(pt * 0.72), color: C.primary, bold: true } });
      runs.push({ text: v.text, options: { fontSize: pt, color: C.text, breakLine: i < vs.length - 1, paraSpaceAfter: 6 } });
    });
    return runs;
  }
  versesHeight(vs, pt, boxW) {
    // the number label is set at 72% size; count it at that width
    const gaps = vs.filter((v, i) => newPassage(vs[i - 1], v)).length * lineH(pt, 1.1);
    return gaps + vs.reduce((h, v, i) => h + Math.max(1, Math.ceil((widthIn(this.verseNum(vs, i) + "  ", pt * 0.72) + widthIn(v.text, pt)) / (boxW * 0.985))) * lineH(pt, 1.1) + 6 / 72, 0);
  }
  cardHeight(vs, w, pt = PT.verse) { return this.versesHeight(vs, pt, w - 0.6) + 0.85; }
  card(s, x, y, w, h, vs, ref, pt = PT.verse) {
    (this.slideVerses[this.count] ||= []).push({ ref, vs });
    s.addShape(this.pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.12, fill: { color: C.card }, line: { color: C.card } });
    // a long multi-book label can't wrap into the verses: fall back to short book names
    if (widthIn(ref, 24) > (w - 0.6) * 0.95) ref = ref.replace(new RegExp(Object.values(BOOK).join("|"), "g"), (b) => SHORT[Object.keys(BOOK).find((k) => BOOK[k] === b)]);
    if (widthIn(ref, 24) > (w - 0.6) * 0.95) throw new Error("card label too long: " + ref);
    s.addText(ref, { x: x + 0.3, y: y + 0.16, w: w - 0.6, h: 0.5, fontFace: FONT, fontSize: 24, bold: true, color: C.primary, margin: 0, isTextBox: true });
    s.addText(this.verseRuns(vs, pt), { x: x + 0.3, y: y + 0.66, w: w - 0.6, h: h - 0.72, fontFace: FONT, valign: "top", lineSpacingMultiple: 1.1, margin: 0, isTextBox: true });
  }
  closingHeight(text) { return text ? lines(text, PT.closing, W - 2 * MX - 0.6) * lineH(PT.closing) + 0.45 : 0; }
  closingBox(s, text, y) {
    const h = this.closingHeight(text);
    s.addShape(this.pres.shapes.ROUNDED_RECTANGLE, { x: MX, y, w: W - 2 * MX, h, rectRadius: 0.1, fill: { color: C.callout }, line: { color: C.callout } });
    s.addText(text, { x: MX + 0.3, y, w: W - 2 * MX - 0.6, h, fontFace: FONT, fontSize: PT.closing, bold: true, color: C.primary, valign: "middle", margin: 0, isTextBox: true });
  }

  // ---- slide types ----
  title({ title, subtitle, footer, paras }) {
    const s = this.newSlide(C.primary);
    s.addShape(this.pres.shapes.OVAL, { x: MX, y: 1.55, w: 0.55, h: 0.55, fill: { color: C.accent }, line: { color: C.accent } });
    s.addText(title, { x: MX, y: 2.3, w: W - 2 * MX, h: 1.4, fontFace: FONT, fontSize: 60, bold: true, color: C.white, margin: 0, isTextBox: true });
    s.addText(subtitle, { x: MX, y: 3.8, w: W - 2 * MX, h: 0.8, fontFace: FONT, fontSize: 34, color: "F1DCE5", margin: 0, isTextBox: true });
    s.addText(footer, { x: MX, y: 5.9, w: W - 2 * MX, h: 0.6, fontFace: FONT, fontSize: 22, color: "E8D5DD", margin: 0, isTextBox: true });
    s.addNotes(this.notesFor(paras));
  }
  // scripture slide(s): full text, split by verse across slides
  scripture({ refs, title, caption, paras, extraNotes }) {
    const vs = verses(refs);
    this.requiredVerses.push(...vs);
    const ref = label(refs);
    const boxW = W - 2 * MX - 0.6;
    const capH = caption ? this.closingHeight(caption) + 0.2 : 0;
    const maxMid = BODY_H - 1.0, maxLast = maxMid - capH;
    const pages = []; let cur = [];
    for (const v of vs) {
      if (this.versesHeight([v], PT.verse, boxW) > maxLast) throw new Error("single verse too long " + ref + v.v);
      if (cur.length && this.versesHeight([...cur, v], PT.verse, boxW) > maxMid) { pages.push(cur); cur = []; }
      cur.push(v);
    }
    // the caption sits under the last page; start a new page if it would be crowded
    if (caption && this.versesHeight(cur, PT.verse, boxW) > maxLast) { const last = cur.pop(); pages.push(cur); cur = [last]; }
    pages.push(cur);
    pages.forEach((pg, i) => {
      const s = this.newSlide();
      if (i === 0) this.sectionChip(s);
      this.titleText(s, (title || ref) + (pages.length > 1 ? `（${i + 1}/${pages.length}）` : ""));
      const cardH = this.cardHeight(pg, W - 2 * MX);
      this.card(s, MX, BODY_TOP, W - 2 * MX, cardH, pg, ref);
      if (caption && i === pages.length - 1) this.closingBox(s, caption, BODY_TOP + cardH + 0.2);
      s.addNotes(i === 0 ? this.notesFor(paras, extraNotes) || `經文：${ref}` : `（經文續）${ref}`);
    });
  }
  // bullets + optional verse card + optional closing line.
  // Layout order: stacked → two columns (bullets | verses) → bullets slide, then verses slide.
  points({ title, items, refs, closing, paras, extraNotes }) {
    const bw = W - 2 * MX, lw = 6.25, rw = bw - lw - 0.35, IV = PT.inlineVerse;
    // Requirement 4: key points, not the script's sentences.
    if (items.length > MAX_POINTS) throw new Error(`"${title}": ${items.length} points; at most ${MAX_POINTS} per slide`);
    const oneLine = (w) => items.every((it) => lines(typeof it === "string" ? it : it.t, PT.body, w - 0.45) === 1);
    const long = items.map((it) => (typeof it === "string" ? it : it.t)).find((t) => lines(t, PT.body, bw - 0.45) > 1);
    if (long) throw new Error(`"${title}": key point does not fit on one line: ${long}`);
    const vs = refs ? verses(refs) : null;
    const avail = BODY_H - (closing ? this.closingHeight(closing) + 0.25 : 0);
    const bh = this.bulletsHeight(items, PT.body, bw);
    let layout;
    if (!vs) layout = bh <= avail ? "plain" : null;
    else if (bh + 0.3 + this.cardHeight(vs, bw, IV) <= avail) layout = "stacked";
    // Beside a verse card the bullets get half the width; only if they still fit on one line.
    else if (oneLine(lw) && this.bulletsHeight(items, PT.body, lw) <= avail && this.cardHeight(vs, rw, IV) <= avail) layout = "columns";
    else layout = "split";
    if (!layout) throw new Error(`points overflow "${title}"`);
    if (layout === "split") {
      this.points({ title, items, closing, paras, extraNotes });
      this.scripture({ refs, title: `${title}（經文）` });
      return;
    }
    if (vs) this.requiredVerses.push(...vs);
    const s = this.newSlide();
    this.sectionChip(s);
    this.titleText(s, title);
    let bottom;
    if (layout === "columns") {
      const lh = this.bulletsHeight(items, PT.body, lw), rh = this.cardHeight(vs, rw, IV);
      s.addText(this.bulletRuns(items, PT.body), { x: MX, y: BODY_TOP, w: lw, h: lh + 0.1, fontFace: FONT, valign: "top", lineSpacingMultiple: 1.15, margin: 0, isTextBox: true });
      this.card(s, MX + lw + 0.35, BODY_TOP, rw, rh, vs, label(refs), IV);
      bottom = BODY_TOP + Math.max(lh, rh);
    } else {
      s.addText(this.bulletRuns(items, PT.body), { x: MX, y: BODY_TOP, w: bw, h: bh + 0.1, fontFace: FONT, valign: "top", lineSpacingMultiple: 1.15, margin: 0, isTextBox: true });
      bottom = BODY_TOP + bh;
      if (vs) { const h = this.cardHeight(vs, bw, IV); this.card(s, MX, bottom + 0.3, bw, h, vs, label(refs), IV); bottom += 0.3 + h; }
    }
    if (closing) this.closingBox(s, closing, bottom + 0.25);
    s.addNotes(this.notesFor(paras, extraNotes));
  }
  statement({ text, sub, paras, extraNotes }) {
    const s = this.newSlide();
    this.sectionChip(s);
    const tw = W - 2 * MX - 1.0;
    const th = lines(text, PT.statement, tw) * lineH(PT.statement, 1.2);
    if (th > 4.6) throw new Error("statement too long " + text);
    const subH = sub ? 1.0 : 0;
    const top = Math.max(1.6, (H - th - subH) / 2);
    s.addShape(this.pres.shapes.OVAL, { x: W / 2 - 0.25, y: top - 0.9, w: 0.5, h: 0.5, fill: { color: C.accent }, line: { color: C.accent } });
    s.addText(text, { x: MX + 0.5, y: top, w: tw, h: th + 0.2, fontFace: FONT, fontSize: PT.statement, bold: true, color: C.primary, align: "center", valign: "middle", lineSpacingMultiple: 1.2, margin: 0, isTextBox: true });
    if (sub) s.addText(sub, { x: MX + 0.5, y: top + th + 0.4, w: tw, h: 1.0, fontFace: FONT, fontSize: 28, color: C.muted, align: "center", margin: 0, isTextBox: true });
    s.addNotes(this.notesFor(paras, extraNotes));
  }
  pair({ title, left, right, closing, paras }) {
    const s = this.newSlide();
    this.sectionChip(s);
    this.titleText(s, title);
    const cw = (W - 2 * MX - 0.4) / 2;
    const sides = [left, right].map((refs) => { const vs = verses(refs); this.requiredVerses.push(...vs); return { refs, vs, h: this.cardHeight(vs, cw) }; });
    const h = Math.max(2.4, ...sides.map((x) => x.h));
    if (h + (closing ? this.closingHeight(closing) + 0.25 : 0) > BODY_H) throw new Error("pair overflow " + title);
    sides.forEach((x, i) => this.card(s, MX + i * (cw + 0.4), BODY_TOP, cw, h, x.vs, label(x.refs)));
    if (closing) this.closingBox(s, closing, BODY_TOP + h + 0.25);
    s.addNotes(this.notesFor(paras) || `經文：${label(left)}；${label(right)}`);
  }
  table({ title, intro, header, rows, colW, closing, paras, pt = PT.table, onePage = false }) {
    const tw = colW.reduce((a, b) => a + b, 0);
    if (Math.abs(tw - (W - 2 * MX)) > 0.05) throw new Error("table width " + tw);
    const rowH = (r) => Math.max(...r.map((c, i) => lines(c, pt, colW[i] - 0.25))) * lineH(pt, 1.05) + 0.22;
    const introH = intro ? lines(intro, 28, W - 2 * MX) * lineH(28) + 0.2 : 0;
    const avail = BODY_H - introH - (closing ? this.closingHeight(closing) + 0.25 : 0);
    const hh = rowH(header);
    const pages = []; let cur = []; let h = hh;
    for (const r of rows) { const rh = rowH(r); if (!onePage && h + rh > avail && cur.length) { pages.push(cur); cur = []; h = hh; } cur.push(r); h += rh; }
    pages.push(cur);
    pages.forEach((pg, pi) => {
      const s = this.newSlide();
      if (pi === 0) this.sectionChip(s);
      this.titleText(s, title + (pages.length > 1 ? `（${pi + 1}/${pages.length}）` : ""));
      let y = BODY_TOP;
      if (intro && pi === 0) { s.addText(intro, { x: MX, y, w: W - 2 * MX, h: introH, fontFace: FONT, fontSize: 28, color: C.text, margin: 0, isTextBox: true }); y += introH; }
      const cell = (t, head, first) => ({ text: t, options: { fontFace: FONT, fontSize: pt, bold: head || first, color: head ? C.white : first ? C.primary : C.text, fill: { color: head ? C.primary : first ? C.card : C.white }, valign: "middle", margin: [4, 8, 4, 8] } });
      const data = [header.map((c) => cell(c, true, false)), ...pg.map((r) => r.map((c, i) => cell(c, false, i === 0)))];
      let heights = [hh, ...pg.map((r) => rowH(r))];
      const total = heights.reduce((a, b) => a + b, 0);
      // rowH is a minimum in PowerPoint; when it can't fit, shrink the minimums and let the text size each row
      if (total > avail) heights = heights.map((x) => ((x * avail) / total) * 0.8);
      s.addTable(data, { x: MX, y, w: W - 2 * MX, colW, rowH: heights, border: { type: "solid", pt: 1, color: C.line } });
      if (closing && pi === pages.length - 1) this.closingBox(s, closing, y + Math.min(total, avail) + 0.25);
      s.addNotes(pi === 0 ? this.notesFor(paras) : "（表格續）");
    });
  }
  question({ title, qs, note, paras }) {
    const s = this.newSlide();
    this.sectionChip(s);
    this.titleText(s, title);
    s.addShape(this.pres.shapes.OVAL, { x: W - MX - 0.6, y: 0.52, w: 0.6, h: 0.6, fill: { color: C.accent }, line: { color: C.accent } });
    s.addText("?", { x: W - MX - 0.6, y: 0.52, w: 0.6, h: 0.6, fontFace: FONT, fontSize: 30, bold: true, color: C.primary, align: "center", valign: "middle", margin: 0, isTextBox: true });
    const bw = W - 2 * MX;
    const runs = qs.map((q, i) => ({ text: q, options: { bullet: { type: "number", indent: 36 }, fontSize: PT.body, color: C.text, paraSpaceAfter: 18, breakLine: i < qs.length - 1 } }));
    const h = qs.reduce((a, q) => a + lines(q, PT.body, bw - 0.5) * lineH(PT.body) + 18 / 72, 0);
    if (h + (note ? 0.8 : 0) > BODY_H) throw new Error("question overflow");
    s.addText(runs, { x: MX, y: 1.5, w: bw, h: h + 0.1, fontFace: FONT, valign: "top", lineSpacingMultiple: 1.15, margin: 0, isTextBox: true });
    if (note) s.addText(note, { x: MX, y: 1.5 + h + 0.4, w: bw, h: 0.6, fontFace: FONT, fontSize: 24, color: C.muted, margin: 0, isTextBox: true });
    s.addNotes(this.notesFor(paras));
  }

  // script copy with every slide marked 【第 N 頁】, the script paragraphs it
  // covers under it, and the full text of any scripture shown on that slide
  writeCrossReference() {
    const bySlide = {};
    this.paras.forEach((p, i) => (bySlide[this.slideOf[i]] ||= []).push(p.replace(/[ \t]*<!--[\s\S]*?-->/g, "")));
    const out = [];
    for (let n = 1; n <= this.count; n++) {
      out.push(`**【第 ${n} 頁】**`);
      out.push(...(bySlide[n] || []));
      for (const { ref, vs } of this.slideVerses[n] || []) {
        const lines = vs.map((v, i) => `> **${this.verseNum(vs, i)}** ${v.text}`);
        out.push(`> 經文：${ref}\n>\n${lines.join("\n>\n")}`);
      }
    }
    fs.writeFileSync(this.meta.crossReference, out.join("\n\n") + "\n");
    return this.meta.crossReference;
  }
  async save(outPath) {
    const missing = this.paras.map((_, i) => i).filter((i) => !this.claimed.has(i));
    if (missing.length) throw new Error("uncovered paragraphs: " + missing.map((i) => `[${i}] ${this.paras[i].slice(0, 30)}`).join(" | "));
    // pptxgenjs tags runs en-US, which makes PowerPoint break lines by Latin rules
    // (。，」 at the start of a line). Tag the text as zh-TW instead.
    const JSZip = require("jszip");
    const zip = await JSZip.loadAsync(await this.pres.write({ outputType: "nodebuffer" }));
    for (const name of Object.keys(zip.files).filter((n) => /^ppt\/(slides|notesSlides)\/.*\.xml$/.test(n))) {
      const xml = await zip.file(name).async("string");
      zip.file(name, xml.replace(/lang="en-US"/g, 'lang="zh-TW" altLang="en-US"'));
    }
    fs.writeFileSync(outPath, await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" }));
    const xref = this.writeCrossReference();
    return {
      slides: this.count,
      verses: [...new Set(this.requiredVerses.map((v) => `${v.book} ${v.ch}:${v.v}`))],
      crossReference: xref,
    };
  }
}
module.exports = { Deck, verses, label };
