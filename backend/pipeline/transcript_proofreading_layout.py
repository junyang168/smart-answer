"""Group timed cues for proofreading without modifying their subtitle timing."""

import re


def visible_text(cues):
    return "".join(re.sub(r"~~.*?~~", "", cue["text"], flags=re.S) for cue in cues).strip()


def short_block(block):
    return block["end"] - block["start"] < 5 or sum(
        char.isalnum() for char in visible_text(block["cues"])
    ) < 15


def join(left, right):
    return {"start": left["start"], "end": max(left["end"], right["end"]),
            "cues": left["cues"] + right["cues"]}


def can_join(left, right):
    return right["start"] - left["end"] <= 5 and max(left["end"], right["end"]) - left["start"] <= 32


def proofreading_blocks(cues):
    """Attach short dependent passages to a nearby block, retaining every cue."""
    initial = []
    for cue in cues:
        block = {"start": cue["start"], "end": cue["end"], "cues": [cue]}
        previous = initial[-1] if initial else None
        if (previous and cue["id"].split("-")[0] == previous["cues"][0]["id"].split("-")[0]
                and cue["start"] - previous["end"] <= 2
                and max(previous["end"], cue["end"]) - previous["start"] <= 27):
            initial[-1] = join(previous, block)
        else:
            initial.append(block)

    result = []
    for i, block in enumerate(initial):
        if not short_block(block):
            result.append(block)
            continue
        left = result[-1] if result else None
        right = initial[i + 1] if i + 1 < len(initial) else None
        can_left = left is not None and can_join(left, block)
        can_right = right is not None and can_join(block, right)
        text = visible_text(block["cues"]).rstrip("」』”")
        incomplete = not text.endswith(("。", "？", "！", "?", "!"))
        if can_right and (incomplete or not can_left):
            initial[i + 1] = join(block, right)
        elif can_left:
            result[-1] = join(left, block)
        else:
            result.append(block)
    if [cue for block in result for cue in block["cues"]] != cues:
        raise ValueError("Proofreading layout changed the ordered subtitle cues")
    return result


def mark_repetition(text, quote, retained):
    """Apply one exact, reversible deletion of adjacent repeated speech."""
    if not quote or not retained.strip() or text.count(quote) != 1 or "~" in quote:
        return text
    size = len(quote) - len(retained)
    if size <= 0:
        return text
    position = text.index(quote)
    if any(m.start() < position + len(quote) and position < m.end()
           for m in re.finditer(r"~~.*?~~", text, flags=re.S)):
        return text

    def spoken(value):
        return "".join(char for char in value if char.isalnum())

    for start in range(len(quote) - size + 1):
        end = start + size
        if quote[:start] + quote[end:] != retained:
            continue
        cut = quote[start:end]
        repeated = spoken(cut)
        if not repeated or re.search(r"[不沒没未無无非勿別别第章節节\d]", cut):
            continue
        left, right = spoken(quote[:start]), spoken(quote[end:])
        # More than one redundant repetition may be removed in a single span.
        if not any(repeated == repeated[:n] * (len(repeated) // n)
                   and (left.endswith(repeated[:n]) or right.startswith(repeated[:n]))
                   for n in range(1, len(repeated) + 1) if len(repeated) % n == 0):
            continue
        a, b = position + start, position + end
        return text[:a] + "~~" + text[a:b] + "~~" + text[b:]
    return text


def proofreading_speaker_text(cues, roles):
    """Render canonical speaker roles while retaining uncertain speech."""
    turns = []
    for cue in cues:
        speaker = cue["speaker"]
        role = roles.get(speaker, "uncertain")
        if role not in {"professor", "audience", "uncertain"}:
            raise ValueError("Unknown proofreading speaker role")
        if turns and turns[-1]["speaker"] == speaker:
            turns[-1]["text"] += cue["text"]
        else:
            turns.append({"speaker": speaker, "role": role, "text": cue["text"]})
    names = {"professor": "教授", "audience": "聽眾"}
    parts = []
    dialogue = any(turn["role"] == "audience" for turn in turns)
    for turn in turns:
        name = names.get(turn["role"])
        label = f"**{name}**：" if name and dialogue else ""
        parts.append(label + turn["text"])
    return "\n\n".join(parts), turns
