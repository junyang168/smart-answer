#!/usr/bin/env python3
"""Check markdown docs: relative link targets exist, in-doc anchors resolve,
mermaid node IDs are not reused with different labels."""
import re, sys, unicodedata
from pathlib import Path

def anchor(heading: str) -> str:
    h = heading.strip().lower()
    out = []
    for ch in h:
        if ch.isalnum() or ch in '-_' or unicodedata.category(ch).startswith('L'):
            out.append(ch)
        elif ch == ' ':
            out.append('-')
    return ''.join(out)

def check(paths):
    bad = []
    for p in paths:
        text = p.read_text(encoding='utf-8')
        anchors = {anchor(m.group(2)) for m in re.finditer(r'^(#{1,6})\s+(.*)$', text, re.M)}
        # links
        for m in re.finditer(r'\[[^\]]*\]\(([^)]+)\)', text):
            tgt = m.group(1).strip()
            if tgt.startswith(('http://', 'https://', 'mailto:')):
                continue
            ln = text[:m.start()].count('\n') + 1
            filepart, _, frag = tgt.partition('#')
            if filepart:
                dest = (p.parent / filepart).resolve()
                if not dest.exists():
                    bad.append(f"{p}:{ln}  missing file: {filepart}")
                    continue
                if frag:
                    dtext = dest.read_text(encoding='utf-8') if dest.is_file() else ''
                    da = {anchor(x.group(2)) for x in re.finditer(r'^(#{1,6})\s+(.*)$', dtext, re.M)}
                    if frag.lower() not in da:
                        bad.append(f"{p}:{ln}  missing anchor {filepart}#{frag}")
            elif frag and frag.lower() not in anchors:
                bad.append(f"{p}:{ln}  missing local anchor #{frag}")
        # erDiagram entities must appear in a table in the same document
        for blk in re.finditer(r'```mermaid\n(erDiagram.*?)```', text, re.S):
            body = blk.group(1)
            start = text[:blk.start()].count('\n') + 1
            ents = set(re.findall(r'^\s*([A-Z][A-Z_]+)\s', body, re.M))
            ents |= set(re.findall(r'--\S*\s+([A-Z][A-Z_]+)\s*:', body))
            for ent in sorted(ents):
                snake = ent.lower()
                # look for the entity outside every mermaid block
                prose = re.sub(r'```mermaid.*?```', '', text, flags=re.S).lower()
                if snake in prose or snake + 's' in prose:
                    continue
                bad.append(f"{p}:{start}  erDiagram entity '{ent}' appears in the diagram but nowhere in the text")

        # mermaid duplicate node ids
        for blk in re.finditer(r'```mermaid\n(.*?)```', text, re.S):
            body = blk.group(1)
            start = text[:blk.start()].count('\n') + 1
            decl = {}
            for nid, label in re.findall(r'\b([A-Za-z][A-Za-z0-9_]*)\s*\[\s*"([^"]*)"\s*\]', body):
                decl.setdefault(nid, set()).add(label)
            for nid, labels in decl.items():
                if len(labels) > 1:
                    bad.append(f"{p}:{start}  mermaid node '{nid}' has {len(labels)} labels: {sorted(labels)}")
    return bad

root = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
files = sorted(root.rglob('*.md'))
bad = check(files)
print(f"checked {len(files)} files")
for b in bad:
    print("  FAIL", b)
print("OK" if not bad else f"{len(bad)} problem(s)")
sys.exit(1 if bad else 0)
