// CJK-aware heading slug for manuscript deep-links.
//
// IMPORTANT: this must stay byte-for-byte equivalent to the Python
// `slugify_heading` in backend/api/sermon_search/slugify.py, because the
// topic API precomputes section anchors with the Python version while the
// manuscript renderer derives heading ids with this one. Change both together.
//
// Unlike slugifySectionTitle (which strips all non-ASCII and is fine for the
// English full-article feature), this keeps CJK / Greek / Hebrew letters so
// Chinese headings produce stable, non-colliding anchors.

const KEEP = "a-z0-9\\u4e00-\\u9fff\\u0370-\\u03ff\\u1f00-\\u1fff\\u0590-\\u05ff";
const NON_KEEP_RE = new RegExp(`[^${KEEP}]+`, "g");

export function slugifyHeadingAnchor(value: string): string {
  return (value || "")
    .trim()
    .toLowerCase()
    .replace(NON_KEEP_RE, "-")
    .replace(/^-+|-+$/g, "");
}
