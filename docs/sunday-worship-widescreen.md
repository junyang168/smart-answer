# Sunday worship widescreen template

The runtime template is `SUNDAY_WORSHIP_DIR/template.pptx`, outside Git. A code
release does not replace it. Keep a private backup before any template change;
replace the file atomically only after rendering generated services against it.
Historical generated decks and manually edited decks are not migration inputs.

## Reproduce the 4:3 to 16:9 migration

`scripts/build-sunday-widescreen-template.mjs` takes an input template, a new
candidate path and the SHA-256 of the reviewed input. Run a private copy beside
the bundled presentation runtime's `node_modules` (`@oai/artifact-tool`, `jszip`).
The reviewed input has 16 slides, a 10 × 7.5-inch canvas, and SHA-256
`f2b6c1a24f6732f1fb44446c2a0254195982c705fddf7f2f9ca6c0fa16f11ae5`.
It refuses a different source or an existing output file.

The output uses 13⅓ × 7½ inches (16:9). Artifact Tool authors the geometry;
the native package is retained to preserve embedded audio, notes, media actions
and relationships. `artifact-draft.pptx` is a comparison artifact, not the
runtime template. `migration.json` records the source/candidate hashes and all
shape plans. The authored candidate must retain every original text node and
all media, notes and relationship parts byte for byte.

The cover has separate bands for church names, prayer, mission, date, address,
contact details and the worker table. The grouped church title uses identity
coordinates so its children remain visible. The address is centered. The
reading introduction reserves a separate table area; each reader row is 0.7
inch high and at most 30pt. Four rows fit within the canvas. Scripture and lyric
body frames have 0.65-inch side margins and use the existing template font.
Images retain their aspect ratios. Footer cues stay inside the canvas.

Lyrics use 1.5-times font-size leading, with the same leading used for fit
measurement and serialized paragraphs. Scripture retains its own leading.
The renderer keeps the 30pt cap, 18pt readability floor, intentional lyric
lines and blank-line slide boundaries. It rejects content that cannot fit.
The fixed Doxology page is an explicit exception requested after preview:
its five short lines use 36pt type with 54pt leading and a separate 48pt title.

## Acceptance and installation

Generate isolated samples with the actual website generator: an ordinary
Sunday, a communion Sunday, multiple hymn slides and four reading assignments.
Render every visible page using installed Chinese fonts. Inspect the cover,
program table, reading introduction, scripture pages, songs and future-worker
table at full size. Preserve all source text and media. Native PowerPoint
inspection is useful when an unlocked desktop is available; headless preview
alone must not be described as a PowerPoint check.

The generic presentation package validator reports an inherited exception on
slide 14: `<a:hlinkClick r:id="" action="ppaction://media"/>`. It is the
existing embedded-media action, also present in the original template. The
[Microsoft Open XML media example](https://learn.microsoft.com/en-us/office/open-xml/presentation/how-to-add-a-video-to-a-slide-in-a-presentation)
uses this empty relationship ID for the media action. Do not remove audio or
invent a relationship to silence this finding. Record the exception, compare
all actual relationship targets and preserve the media package unchanged.

After acceptance, verify the current runtime template still matches the
reviewed input hash, back it up outside the public data tree, stage the exact
candidate bytes in the target directory, and use `os.replace` to switch it.
Recheck the installed hash and 16:9 canvas. Code changes, including lyric
leading, still ship only through `scripts/deploy.sh` as described in the
operations runbook. New PPT generation uses the template; existing downloaded
decks need regeneration to use the new layout.
