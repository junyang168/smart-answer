"""Check generated white SVG text in the browser used to display it."""

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def measure(svgs: dict[str, str]) -> dict:
    """Only pass XML produced by slide_visual_evidence.render_svg."""
    if not svgs:
        return {}
    chrome = (
        os.environ.get("CHROME_BIN")
        or shutil.which("chromium")
        or shutil.which("google-chrome")
        or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder)
        page = directory / "measure.html"
        body = "".join(
            f'<section id="{html.escape(key, quote=True)}">{svg}</section>'
            for key, svg in svgs.items()
        )
        page.write_text(
            body
            + """<pre id="result"></pre><script>
const result = {};
for (const section of document.querySelectorAll('section')) {
  const svg = section.querySelector('svg'), frame = svg.viewBox.baseVal;
  const boxes = Array.from(svg.querySelectorAll('text tspan'), t => {
    const b = t.getBBox();
    return {id:t.parentNode.id,x:b.x,y:b.y,right:b.x+b.width,bottom:b.y+b.height};
  });
  const outside = boxes.filter(b => b.x < -0.5 || b.y < -0.5 ||
      b.right > frame.width+0.5 || b.bottom > frame.height+0.5);
  const overlaps = [];
  for (const [i,a] of boxes.entries()) for (const b of boxes.slice(i+1)) {
    const dx = Math.min(a.right,b.right)-Math.max(a.x,b.x);
    const dy = Math.min(a.bottom,b.bottom)-Math.max(a.y,b.y);
    if (dx>2 && dy>0.22*Math.min(a.bottom-a.y,b.bottom-b.y))
      overlaps.push([a.id,b.id]);
  }
  result[section.id] = {outside,overlaps};
}
document.querySelector('#result').textContent = JSON.stringify(result);
</script>""",
            encoding="utf-8",
        )
        output = subprocess.run(
            [
                chrome,
                "--headless",
                "--disable-gpu",
                "--no-first-run",
                f"--user-data-dir={directory / 'profile'}",
                "--dump-dom",
                page.as_uri(),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=90,
        ).stdout
        match = re.search(r'<pre id="result">(.*?)</pre>', output, re.S)
        results = json.loads(html.unescape(match[1])) if match else {}
        if set(results) != set(svgs):
            raise ValueError("Incomplete SVG browser measurements")
        return results
