// A soft deletion is stored as ~~text~~. Markdown's own strikethrough decides
// pairs by punctuation rules that misread Chinese text: in 「不聽話。~~好，」 the
// closing ~~ follows 。 and is taken as an opener, so 1,835 kept characters of
// 2011 NYSC 二-1 rendered as deleted. Pair the marks strictly in order instead.
export function pairSoftDeletions(text: string): string {
  return text.replace(/~~([\s\S]+?)~~/g, "<del>$1</del>");
}
