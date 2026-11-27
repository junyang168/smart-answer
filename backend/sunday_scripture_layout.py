"""Use the actual template and rendered line widths to paginate scripture."""
from functools import lru_cache
from pptx import Presentation
from backend import ppt_text_layout as layout


@lru_cache(maxsize=8)
def _frame(path, modified):
    from backend.api.ppt_generator import _iter_text_frames
    deck = Presentation(path)
    for slide in deck.slides:
        for frame in _iter_text_frames(slide.shapes):
            if '{scriptureVerses}' in frame.text:
                return frame
    raise ValueError('PPT 樣板缺少讀經文字框，無法安全分頁。')


def scripture_frame(template_path=None):
    from backend.api.config import PPT_TEMPLATE_FILE
    path = template_path if template_path is not None else PPT_TEMPLATE_FILE
    return _frame(str(path), path.stat().st_mtime_ns)


def verse_line(verse):
    return f"{verse.get('chapter', 1)}:{verse.get('verse', 1)} {verse.get('text') or ''}"


def paginate(verses, frame=None):
    if not verses:
        return []
    frame = frame if frame is not None else scripture_frame()
    family = layout.template_font(frame)
    width, _ = layout.frame_extent(frame)
    width = (width-frame.margin_left-frame.margin_right)/12700*.98
    weights = []
    for verse in verses:
        visible = verse_line(verse).split(':',1)[1]
        lines = layout.wrapped_lines(visible,width,family,layout.MAX_BODY_PT)
        weights.append((lines or 1)*layout.MAX_BODY_PT*layout.LINE_HEIGHT + layout.SCRIPTURE_GAP_PT)
    # Minimize pages first, then balance their measured visual heights. A short
    # passage keeps its own slide. Never combine unrelated scripture sections.
    count=len(verses); best={count:(0,0,[])}
    for start in range(count-1,-1,-1):
        choices=[]
        for end in range(min(count,start+7),start,-1):
            lines=[verse_line(v) for v in verses[start:end]]
            if not layout.fits(frame,lines,family,layout.MAX_BODY_PT,scripture=True):
                # An exceptionally long single verse may shrink; whole verses
                # remain intact, and the renderer still checks overflow.
                if end!=start+1 or not any(layout.fits(frame,lines,family,size,scripture=True)
                                         for size in range(layout.MAX_BODY_PT-1,layout.MIN_BODY_PT-1,-1)):
                    continue
            if end in best:
                pages,cost,tail=best[end]
                choices.append((pages+1,cost+sum(weights[start:end])**2,[verses[start:end],*tail]))
        if choices:
            best[start]=min(choices,key=lambda item:(item[0],item[1]))
    if 0 not in best:
        raise layout.PptTextOverflowError('單節讀經經文過長，無法安全分頁；請管理員核對。')
    return best[0][2]


MAX_READERS = 4
MAX_VERSES_PER_READER = 8


def reader_counts(verse_counts):
    import math
    counts=[math.ceil(n/MAX_VERSES_PER_READER) for n in verse_counts]
    if sum(counts)>MAX_READERS:
        raise ValueError('讀經安排超過上限：最多 4 位同工，每人最多 8 節；請管理員調整經文安排。')
    return counts


def reader_groups(sections, reader_count):
    passages=[(i,section.get('verses') or []) for i,section in enumerate(sections) if section.get('verses')]
    counts=reader_counts([len(verses) for _,verses in passages])
    minimum=sum(counts)
    if reader_count>MAX_READERS:
        raise ValueError('讀經同工最多 4 位。')
    if reader_count<minimum:
        raise ValueError(f'讀經同工人數不足：每人最多 8 節，這些經文至少需要 {minimum} 位讀經同工。')
    target=min(reader_count,sum(len(verses) for _,verses in passages))
    while sum(counts)<target:
        choices=[i for i,(_,verses) in enumerate(passages) if counts[i]<len(verses)]
        index=max(choices,key=lambda i:len(passages[i][1])/counts[i])
        counts[index]+=1
    result=[]
    for (section,verses),count in zip(passages,counts):
        base,extra=divmod(len(verses),count);start=0
        for number in range(count):
            end=start+base+(number<extra)
            result.append({'section':section,'verses':verses[start:end],'reader_index':len(result)})
            start=end
    return result
