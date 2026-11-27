"""Flow the worship report blocks using their rendered text, not template slots."""
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE
from pptx.util import Pt
from backend.ppt_text_layout import PptTextOverflowError, template_font, wrapped_lines


def report_blocks(presentation):
    for slide in presentation.slides:
        text_shapes = [s for s in slide.shapes if getattr(s, 'has_text_frame', False)]
        announcements = next((s for s in text_shapes if '{announcements}' in s.text), None)
        prayer = next((s for s in text_shapes if '{healthPrayer}' in s.text), None)
        support = next((s for s in text_shapes if s.text.startswith('事工支持奉獻')), None)
        tables = [s for s in slide.shapes if getattr(s, 'has_table', False)]
        if announcements is not None and prayer is not None and support is not None and tables:
            yield (announcements, prayer, support), min(s.top for s in tables)


def measured_height(shape):
    frame = shape.text_frame
    family = template_font(frame)
    frame.margin_top = frame.margin_bottom = 0
    frame.paragraphs[-1].space_after = Pt(0)
    total = 0
    for paragraph in frame.paragraphs:
        sizes = [r.font.size.pt for r in paragraph.runs if r.font.size is not None]
        size = max(sizes, default=paragraph.font.size.pt if paragraph.font.size else 18)
        properties = paragraph._p.pPr
        indent = int(properties.get('marL', '0')) / 12700 if properties is not None else 0
        # Template's inherited top-level diamond bullet also occupies horizontal space.
        if properties is not None and properties.find('{http://schemas.openxmlformats.org/drawingml/2006/main}buChar') is not None:
            indent = max(indent, size)
        width = (shape.width-frame.margin_left-frame.margin_right)/12700 - indent - 4
        count = 0
        for line in paragraph.text.splitlines() or ['']:
            wrapped = wrapped_lines(line, width*.97, family, size)
            if wrapped is None:
                raise PptTextOverflowError('家事報告有無法容納的長文字，請調整換行。')
            count += wrapped
        paragraph.line_spacing = Pt(size*1.2)
        total += count*size*1.2
        total += (paragraph.space_before.pt if paragraph.space_before else 0)
        total += (paragraph.space_after.pt if paragraph.space_after else 0)
    return Pt(total + 2)  # Office glyph and rounding clearance.


def flow_report_blocks(blocks):
    for shapes, boundary in blocks:
        heights = [measured_height(shape) for shape in shapes]
        gap = Pt(8)
        top = shapes[0].top
        if top + sum(heights) + gap*len(shapes) > boundary:
            raise PptTextOverflowError('家事報告與代禱內容超出排班表上方的可用空間，請精簡內容或分頁；未產生重疊的 PPT。')
        for shape, height in zip(shapes, heights):
            shape.top, shape.height = top, height
            shape.text_frame.vertical_anchor = MSO_ANCHOR.TOP
            shape.text_frame.auto_size = MSO_AUTO_SIZE.NONE
            shape.text_frame.word_wrap = True
            top += height + gap
