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
