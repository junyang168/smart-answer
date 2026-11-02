"""Deterministic text sizing for the existing editable Sunday PPT template.

Measure the requested template font where installed; use conservative glyph
widths otherwise. Never enlarge the shape or depend on Office auto-shrink.
"""
from functools import lru_cache
import os
from pathlib import Path
import re
import unicodedata

from PIL import ImageFont
from pptx.oxml.xmlchemy import OxmlElement
from pptx.enum.text import MSO_AUTO_SIZE, MSO_ANCHOR
from pptx.util import Pt

MAX_BODY_PT = 30
MIN_BODY_PT = 18
LINE_HEIGHT = 1.10
_SCALE = 4
_TOKEN = re.compile(r"\s+|[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*|.")


class PptTextOverflowError(ValueError):
    """The content cannot fit safely without changing the requested slide split."""


def template_font(text_frame):
    for paragraph in text_frame.paragraphs:
        for run in paragraph.runs:
            east = run._r.xpath('./a:rPr/a:ea')
            for name in ([east[0].get('typeface')] if east else []) + [run.font.name]:
                if name and not name.startswith('+'):
                    return name
        if paragraph.font.name and not paragraph.font.name.startswith('+'):
            return paragraph.font.name
    return 'DengXian'  # Existing Sunday template's body font, not a replacement.


@lru_cache(maxsize=32)
def font_file(family):
    override = os.getenv('SUNDAY_PPT_FONT_FILE')
    if override:
        candidates = [Path(override)]
    else:
        # Office fonts are not included in macOS's standard font directories.
        names = ['Dengb.ttf', 'Deng.ttf'] if family in {'DengXian', '等线', '等線'} else []
        directories = [Path('/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts'),
                       Path('/Applications/Microsoft Word.app/Contents/Resources/DFonts'),
                       Path.home()/'Library/Fonts', Path('/Library/Fonts'),
                       Path('/usr/share/fonts/truetype'), Path(os.getenv('WINDIR','C:/Windows'))/'Fonts']
        candidates = [folder/name for folder in directories for name in names]
    for path in candidates:
        if path.is_file():
            try:
                name = ImageFont.truetype(str(path), 40).getname()[0]
                if name.casefold() == family.casefold() or (name == 'DengXian' and family in {'等线','等線'}):
                    return str(path)
            except OSError:
                continue
    # Match other templates through python-pptx's installed-font index.
    try:
        from pptx.text.fonts import FontFiles
        return FontFiles.find(family, True, False)
    except (KeyError, OSError):
        return None


@lru_cache(maxsize=128)
def _font(family, size):
    path = font_file(family)
    return ImageFont.truetype(path, size*_SCALE) if path else None


def width_points(text, family, size):
    if '\t' in text:
        return sum(width_points(part, family, size) for part in text.split('\t')) + text.count('\t')*6*size
    font = _font(family,size)
    if font:
        return font.getlength(text)/_SCALE
    # Keep the requested font in PPT; do not silently substitute its typeface.
    return sum(size*(6 if c == '\t' else .5 if c.isspace() else 1.0)
               for c in text if not unicodedata.combining(c))


def wrapped_lines(text, width, family, size):
    count, used = 1, 0.0
    for token in _TOKEN.findall(text):
        advance = (sum(size*6 if c == '\t' else width_points(c,family,size) for c in token)
                   if '\t' in token else width_points(token,family,size))
        if advance > width:
            return None
        if used + advance > width:
            count += 1
            used = 0.0
            if token.isspace():
                continue
        used += advance
    return count


def frame_extent(text_frame):
    owner = text_frame._parent
    if hasattr(owner, 'width') and hasattr(owner, 'height'):
        return owner.width, owner.height
    # _iter_text_frames also supports table cells, including merged cells.
    table = owner._parent
    for row_index, row in enumerate(table.rows):
        for column_index, cell in enumerate(row.cells):
            if cell._tc is owner._tc:
                return (sum(table.columns[j].width for j in range(column_index,column_index+owner.span_width)),
                        sum(table.rows[i].height for i in range(row_index,row_index+owner.span_height)))
    raise PptTextOverflowError('無法核對文字框的有效範圍')


def fits(text_frame, lines, family, size, *, scripture=False):
    frame_width, frame_height = frame_extent(text_frame)
    width = (frame_width-text_frame.margin_left-text_frame.margin_right)/12700*.98
    height = (frame_height-text_frame.margin_top-text_frame.margin_bottom)/12700*.98
    if width <= 0 or height <= 0:
        return False
    total = 0
    for line in lines:
        # Scripture's visible prefix is the verse number, not chapter:verse.
        visible = re.sub(r'^\d+:(\d+)\s+', r'\1 ', line) if scripture else line
        if scripture:
            wrapped = wrapped_lines(visible,width,family,size)
            if wrapped is None:
                return False
            total += wrapped
        else:
            # Song line breaks are intentional: shrink the whole slide until
            # every complete source line fits, never wrap it into another line.
            if width_points(visible,family,size) > width:
                return False
            total += 1
    gap = 1.5 if scripture else 0
    return total*size*LINE_HEIGHT + len(lines)*gap <= height


def prepare_frame(text_frame, lines, *, scripture=False):
    family = template_font(text_frame)  # Capture before clear() drops run styles.
    size = next((size for size in range(MAX_BODY_PT,MIN_BODY_PT-1,-1)
                 if fits(text_frame,lines,family,size,scripture=scripture)), None)
    if size is None:
        raise PptTextOverflowError('此投影片文字過多或單行過長，請調整換行／空行分頁；不能以溢出的文字產生 PPT')
    text_frame.auto_size = MSO_AUTO_SIZE.NONE
    text_frame.word_wrap = scripture
    text_frame.vertical_anchor = MSO_ANCHOR.TOP if scripture else MSO_ANCHOR.MIDDLE
    text_frame.clear()
    return family,size


def format_paragraph(paragraph, family, size, *, scripture=False):
    paragraph.font.name = family
    paragraph.font.size = Pt(size)
    paragraph.font.bold = True
    paragraph.space_before = Pt(0)
    paragraph.space_after = Pt(1.5 if scripture else 0)
    paragraph.line_spacing = Pt(size*LINE_HEIGHT)
    ppr = paragraph._p.get_or_add_pPr()
    ppr.set('marL','0');ppr.set('marR','0');ppr.set('indent','0')
    for element in paragraph._p.xpath('./a:endParaRPr'):
        paragraph._p.remove(element)
    _set_east_asian(paragraph.font._element,family)


def format_run(run, family, size, color, *, verse=False):
    run.font.name = family
    run.font.size = Pt(size)  # Office applies superscript scaling from baseline.
    run.font.bold = True
    run.font.color.rgb = color
    rpr = run._r.get_or_add_rPr()
    _set_east_asian(rpr,family)
    # Font.superscript is not a python-pptx property; use serialized DrawingML.
    if verse:
        rpr.set('baseline','30000')


def _set_east_asian(properties, family):
    east = properties.find('{http://schemas.openxmlformats.org/drawingml/2006/main}ea')
    if east is None:
        east = OxmlElement('a:ea');properties.append(east)
    east.set('typeface',family)
