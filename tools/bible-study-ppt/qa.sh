#!/bin/bash
# qa.sh <deck.pptx> <qa dir>
# Render the deck in PowerPoint (the program the owner presents with; other
# renderers measure Microsoft JhengHei differently and miss real overflow),
# split the PDF into pages and write contact sheets <qa dir>/sheetNN.jpg.
set -e
SRC="$1"; Q="$(mkdir -p "$2" && cd "$2" && pwd)"
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../.." && pwd)
C=~/Library/Containers/com.microsoft.Powerpoint/Data/Documents/qa; mkdir -p "$C"
N=$(basename "$Q"); cp "$SRC" "$C/$N.pptx"; rm -f "$C/$N.pdf"
osascript <<OSA >/dev/null
tell application "Microsoft PowerPoint"
  open POSIX file "$C/$N.pptx"
  delay 4
  save active presentation in POSIX file "$C/$N.pdf" as save as PDF
  delay 2
  close active presentation saving no
end tell
OSA
rm -rf "$Q"; mkdir -p "$Q/img"
swift "$REPO/backend/reference_commentary/split_pdf.swift" "$C/$N.pdf" "$Q/img" >/dev/null
cd "$Q/img"; for f in page-*.jpg; do n=$(echo $f | sed 's/page-0*\([0-9]*\).jpg/\1/'); sips -Z 1600 "$f" --out "s$n.jpeg" >/dev/null; rm "$f"; done
"$REPO/.venv/bin/python" "$HERE/sheet.py" "$Q" 6
