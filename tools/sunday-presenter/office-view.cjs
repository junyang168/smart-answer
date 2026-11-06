// Select Office's chromeless viewer before it starts an editing/coauthoring session.
// This is a playback default, not a change to the owner's sharing permissions.
function isOfficeDocument(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' &&
      (url.hostname === 'onedrive.live.com' || url.hostname.endsWith('.sharepoint.com')) &&
      /\/doc2?\.aspx$/i.test(url.pathname);
  } catch { return false; }
}

function viewDocumentURL(value) {
  if (!isOfficeDocument(value)) return value;
  const url = new URL(value);
  const actions = [...url.searchParams].filter(([key]) => key.toLowerCase() === 'action');
  if (actions.length === 1 && ['embedview', 'present'].includes(actions[0][1].toLowerCase())) return value;
  for (const [key] of actions) url.searchParams.delete(key);
  // ReadingView retains Office's title/status bars in the transmitted output.
  // embedview lets Office render its own chromeless viewer, without cropping
  // slides or injecting CSS into the document's cross-origin frame.
  url.searchParams.set('action', 'embedview');
  return url.href;
}

function installOfficeView(officeSession) {
  officeSession.webRequest.onBeforeRequest({ urls: ['https://onedrive.live.com/*', 'https://*.sharepoint.com/*'] }, (details, callback) => {
    // Preserve authentication/Office POST bodies and every subframe. Office
    // slideshow popups must retain their original opener and session state.
    const target = details.resourceType === 'mainFrame' && details.method === 'GET'
      ? viewDocumentURL(details.url) : details.url;
    callback(target === details.url ? {} : { redirectURL: target });
  });
}

module.exports = { isOfficeDocument, viewDocumentURL, installOfficeView };
