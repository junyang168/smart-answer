// Browser-native fullscreen for the live Office slide surface. Original
// rendering, animations and keyboard handlers remain in the same document.
function officeSlideShowActive(document) {
  return !!document.fullscreenElement && document.fullscreenElement === document.getElementById('SlidePanel');
}
async function enterOfficeSlideShow(document) {
  const panel = document.getElementById('SlidePanel');
  if (!panel) return false;
  await panel.requestFullscreen();
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

