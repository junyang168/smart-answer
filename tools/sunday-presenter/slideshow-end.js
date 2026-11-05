// Runs inside each document/frame. No Node or controller bridge is exposed.
function protectSlideshowEnd() {
  if (window.__sundayEndProtected) return;
  window.__sundayEndProtected = true;
  const endings = new Set([
    'end of slide show, click to exit.',
    'end of slide show, click to exit',
    '幻灯片放映结束，单击退出。',
    '幻灯片放映结束，单击退出',
    '投影片放映結束，按一下以結束。',
    '放映結束，按一下以結束。',
  ]);
  const atEnd = () => {
    if (!document.body) return false;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!endings.has(node.textContent.trim().replace(/\s+/g, ' ').toLowerCase())) continue;
      const element = node.parentElement;
      if (!element || element.closest('[hidden], [aria-hidden="true"]')) continue;
      if (!element.checkVisibility({opacityProperty:true, visibilityProperty:true})) continue;
      const bounds = element.getBoundingClientRect();
      if (bounds.width > 0 && bounds.height > 0 && bounds.bottom > 0 && bounds.right > 0 &&
          bounds.top < innerHeight && bounds.left < innerWidth) return true;
    }
    return false;
  };
  const stop = event => { event.preventDefault(); event.stopImmediatePropagation(); };
  // Keep PowerPoint's end screen alive. Left/Up/PageUp still return to the deck;
  // Escape and explicit toolbar controls remain the user's way to end the show.
  for (const type of ['keydown', 'keyup']) window.addEventListener(type, event => {
    if (['ArrowRight', 'ArrowDown', 'PageDown', ' ', 'Enter'].includes(event.key) &&
        !event.metaKey && !event.ctrlKey && !event.altKey && atEnd()) stop(event);
  }, true);
  for (const type of ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click', 'dblclick', 'touchstart', 'touchend']) {
    window.addEventListener(type, event => {
      if (!event.target?.closest?.('button, a, [role="button"], input, select, textarea') && atEnd()) stop(event);
    }, { capture: true, passive: false });
  }
}

if (typeof module !== 'undefined') module.exports = { protectSlideshowEnd };
