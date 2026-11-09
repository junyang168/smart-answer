// Browser-native fullscreen for the live Office slide surface. Original
// rendering, animations and keyboard handlers remain in the same document.
function officeSlideShowActive(document) {
  return !!document.fullscreenElement && document.fullscreenElement === document.getElementById('SlidePanel');
}
async function enterOfficeSlideShow(document) {
  const panel = document.getElementById('SlidePanel');
  if (!panel) return false;
  await panel.requestFullscreen();
  const view = document.defaultView;
  if (view?.requestAnimationFrame) await new Promise(resolve => view.requestAnimationFrame(() => view.requestAnimationFrame(resolve)));
  return document.fullscreenElement === panel;
}
async function runOfficeAction(frame, script, gesture = false, timeoutMs = 3000) {
  let timer;
  try {
    return await Promise.race([
      frame.executeJavaScript(script, gesture),
      new Promise((_, reject) => { timer = setTimeout(() => reject(Error('PowerPoint 放映操作逾時，請重試。')), timeoutMs); }),
    ]);
  } finally { clearTimeout(timer); }
}
module.exports = { enterOfficeSlideShow, officeSlideShowActive, runOfficeAction };


// Inspect DOM, not an optimistic application flag. The supported Office reading
// surface must be laid out and contain actual slide-rendering content.
function officeSurfaceState(document) {
  const panel = document.getElementById('SlidePanel');
  if (!panel) return { ready: false, fullscreen: false };
  const bounds = panel.getBoundingClientRect();
  const images = [...panel.querySelectorAll('img')].filter(image => {
    if (!image.currentSrc && !image.getAttribute?.('src') && !image.src) return false;
    const style = document.defaultView?.getComputedStyle(image);
    return image.getBoundingClientRect().width > 0 && (!style || style.visibility !== 'hidden' && style.display !== 'none');
  });
  const canvas = [...panel.querySelectorAll('canvas')].some(item => item.width > 0 && item.height > 0);
  const svg = [...panel.querySelectorAll('svg')].some(item => item.getBoundingClientRect().width > 0);
  const rendered = canvas || svg || images.some(image => image.complete && image.naturalWidth > 0);
  const pendingImages = images.some(image => !image.complete || !image.naturalWidth);
  let slideIndex = null;
  for (const button of document.querySelectorAll('button,[role="button"]')) {
    const text = (button.textContent || '').replace(/\s+/g, ' ').trim();
    const match = /^SLIDE\s+(\d+)\s+OF\s+\d+$/i.exec(text);
    if (match) { slideIndex = Number(match[1]); break; }
  }
  return { slideIndex, ready: bounds.width > 0 && bounds.height > 0 && panel.getAttribute('aria-busy') !== 'true' && rendered && !pendingImages,
    fullscreen: document.fullscreenElement === panel };
}
module.exports.officeSurfaceState = officeSurfaceState;
