// Operates only inside the owner's dated OneDrive folder, using visible file names.
function identifyFiles(names) {
  const files = [...new Set(names.filter(n => typeof n === 'string' && /\.pptx?$/i.test(n) && !n.startsWith('~$')))];
  const worship = files.filter(n => /主日崇拜|主日敬拜|^敬拜/.test(n));
  const rest = files.filter(n => !worship.includes(n));
  return worship.length === 1 && rest.length === 1 ? { worship: worship[0], sermon: rest[0] } : null;
}
function fileElements() {
  return [...document.querySelectorAll('a,button,[role="button"],[data-automationid="FieldRenderer-name"]')]
    .filter(e => e.getBoundingClientRect().height > 0)
    .map(e => ({ element: e, name: (e.innerText || '').trim() }))
    .filter(e => /^[^\n]+\.pptx?$/i.test(e.name));
}
function isDocumentURL(value) {
  try {
    const u = new URL(value);
    return u.protocol === 'https:' && (u.hostname === 'onedrive.live.com' ||
      u.hostname.endsWith('.sharepoint.com')) && /\/doc2?\.aspx$/i.test(u.pathname);
  } catch { return false; }
}
function sameFolderURL(actual, expected) {
  try { const a = new URL(actual), b = new URL(expected);
    return a.origin === b.origin && a.pathname === b.pathname && !!b.searchParams.get('id') && a.searchParams.get('id') === b.searchParams.get('id');
  } catch { return false; }
}
function cardPage(state, deck) {
  const pages = state.pages.filter(p => p.deck === deck);
  return pages.find(p => p.id === state.preview) ||
    pages.find(p => p.id === state.selected[deck] && p.ready) ||
    [...pages].reverse().find(p => !p.ready && /\.pptx?\b/i.test(p.title)) ||
    pages.find(p => p.id === state.selected[deck]) || pages.at(-1);
}
if (typeof module !== 'undefined') module.exports = { identifyFiles, fileElements, isDocumentURL, sameFolderURL, cardPage };
else window.presenterCards = { cardPage };

function documentKey(value) {
  if (!isDocumentURL(value)) return null;
  const url = new URL(value);
  const params = Object.fromEntries([...url.searchParams].map(([key, value]) => [key.toLowerCase(), value]));
  const id = (params.sourcedoc || params.resid || '').replace(/[{}]/g, '').toLowerCase();
  if (!/^(?:[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}|[a-f0-9]+![a-f0-9]+)$/.test(id)) return null;
  return `${url.origin}${url.pathname.split(/\/_layouts\//i)[0]}:${id}`;
}
if (typeof module !== 'undefined') module.exports.documentKey = documentKey;
