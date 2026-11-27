// Vector source -> standard macOS iconset. Uses the repository's existing sharp.
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const sharp = require('../../web/node_modules/sharp');

(async () => {
  const assets = path.join(__dirname, 'assets');
  const folder = path.join(__dirname, 'output', 'SundayPresenter.iconset');
  fs.mkdirSync(folder, { recursive: true });
  for (const size of [16, 32, 128, 256, 512]) {
    for (const scale of [1, 2]) {
      await sharp(path.join(assets, 'icon.svg')).resize(size * scale).png()
        .toFile(path.join(folder, `icon_${size}x${size}${scale === 2 ? '@2x' : ''}.png`));
    }
  }
  execFileSync('iconutil', ['-c', 'icns', folder, '-o', path.join(assets, 'SundayPresenter.icns')]);
})().catch(error => { console.error(error); process.exitCode = 1; });
