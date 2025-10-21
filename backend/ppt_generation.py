import re
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Pt


NUMBERED_POINT_PATTERN = re.compile(r"^(\d+)\.\s+(.*)")
_PLACEHOLDER_CANDIDATES = [
    "BODY",
    "CONTENT",
    "OBJECT",
    "TEXT",
    "SUBTITLE",
]
ALLOWED_PLACEHOLDER_TYPES = {
    getattr(PP_PLACEHOLDER, name)
    for name in _PLACEHOLDER_CANDIDATES
    if hasattr(PP_PLACEHOLDER, name)
}


def _remove_slide(presentation, index):
    """Remove a slide from the presentation by index."""
    slide_id_list = presentation.slides._sldIdLst
    slide_ids = list(slide_id_list)
    if index < 0 or index >= len(slide_ids):
        return
    slide = slide_ids[index]
    r_id = slide.rId
    presentation.part.drop_rel(r_id)
    slide_id_list.remove(slide)


def _remove_bullet_nodes(paragraph, *tags):
    p_pr = paragraph._element.get_or_add_pPr()
    for tag in tags:
        node = p_pr.find(qn(tag))
        if node is not None:
            p_pr.remove(node)
    return p_pr


def _apply_bullet_paragraph(paragraph, level):
    p_pr = _remove_bullet_nodes(paragraph, "a:buAutoNum", "a:buNone")
    paragraph.level = level
    return p_pr


def _apply_numbered_paragraph(paragraph, level, start_at=None):
    p_pr = _remove_bullet_nodes(paragraph, "a:buChar", "a:buAutoNum", "a:buNone")
    bu_auto_num = OxmlElement("a:buAutoNum")
    bu_auto_num.set("type", "arabicPeriod")
    if start_at and start_at > 1:
        bu_auto_num.set("startAt", str(start_at))
    p_pr.insert(0, bu_auto_num)
    paragraph.level = level


def _apply_plain_paragraph(paragraph):
    p_pr = _remove_bullet_nodes(paragraph, "a:buChar", "a:buAutoNum", "a:buNone")
    bu_none = OxmlElement("a:buNone")
    p_pr.insert(0, bu_none)
    paragraph.level = 0

# --- CONFIGURATION: CHANGE THIS VARIABLE ---
# Set this to the slide number in your template that has the layout you want to use.
# For example, if it's the 3rd slide in your template file, set this to 3.
TEMPLATE_SLIDE_NUMBER = 2 
# -------------------------------------------


# --- The content for the presentation (same as before) ---
slides_content = [
    # The first slide (Title Slide) will use the default title layout from your template
    ("標題頁", [
        "羅馬書概論：保羅為何寫這封信？",
        "大衛鮑森新約綜覽系列",
    ]),
    # The rest of the slides will use the layout from TEMPLATE_SLIDE_NUMBER
    ("一封「不尋常」的書信", [
        "古代最長的一封信",
        "    - 當時書信平均長度約18-200字",
        "    - 羅馬書長達7000多字，創下紀錄",
        "獨特的寫作風格",
        "    - 超長的問候：為了與不熟悉的教會建立關係",
        "    - 教導性極強：側重教義辯論",
        "    - 充滿對話與辯論：『我們豈可仍在罪中，叫恩典顯多嗎？斷乎不可！』"
    ]),
    ("一個核心問題：為何寫給羅馬？", [
        "保羅的特殊之舉",
        "    - 他寫了一封最長的信，給一間他從未探訪過、也非他建立的教會。",
        "我們要解答的問題：",
        "    - 保羅為何不等親自拜訪時再教導？",
        "    - 他寫這封信的真正用意是什麼？"
    ]),
    ("寫作目的解析 (一)：從「作者」角度", [
        "理論背景：保羅完成了地中海東岸宣教，計劃向西方的西班牙前進。",
        "可能原因 1：一份信仰聲明 (Statement)",
        "    - 為他所傳的福音信息留下一份系統性的文字紀錄。",
        "可能原因 2：一場護教辯論 (Argument)",
        "    - 記錄並回答多年來他所遇到的各種反對聲音。"
    ]),
    ("對「作者理論」的挑戰", [
        "這個理論無法完全解釋：",
        "1. 為何只寄給羅馬？",
        "2. 並非完整的福音：缺少了天國、教會、聖餐等主題。",
        "3. 無法解釋第9-11章：關於以色列的長篇論述看似「離題」。"
    ]),
    ("寫作目的解析 (二)：從「作者與讀者」角度", [
        "理論：保羅寫信給羅馬有其戰略性考量。",
        "可能原因 1：帝國之都 (Capital of the Empire)",
        "    - 在帝國的心臟建立一個穩固的信仰中心。",
        "可能原因 2：通往西方之門 (Gateway to the West)",
        "    - 羅馬教會可成為他西進宣教的新基地，提供支持。"
    ]),
    ("寫作目的解析 (三)：從「讀者」角度 (最關鍵)", [
        "核心觀點：保羅寫信是為了滿足羅馬教會當時特定的需要。",
        "外在處境 (External Situation):",
        "    - 羅馬城在政治與社會上的極度敗壞。",
        "    - 福音是解決這一切社會問題的唯一答案。",
        "內在處境 (Internal Situation):",
        "    - 教會內部的分裂與緊張關係，是解開羅馬書的真正鑰匙。"
    ]),
    ("羅馬教會的內部危機：一把解鎖的鑰匙", [
        "教會的歷史背景：",
        "1. 初期：由猶太基督徒為主建立。",
        "2. 轉變：革老丟皇帝將所有猶太人驅逐出羅馬。",
        "3. 現況：教會由外邦基督徒接管並發展壯大。",
        "4. 衝突：猶太基督徒重返羅馬，發現教會已由外邦人主導，產生緊張。"
    ]),
    ("保羅的解決方案：整卷書的脈絡", [
        "你們都是罪人 (1-3章): 在神面前，猶太人與外邦人沒有分別。",
        "你們都因信稱義 (3-5章): 得救的方式完全一樣。",
        "你們都有各自的軟弱 (6-8章): 外邦人傾向「放縱」，猶太人傾向「律法主義」。"
    ]),
    ("全書的高峰：第9至11章的奧秘", [
        "駁斥「取代神學」：上帝從未棄絕祂的選民以色列。",
        "橄欖樹的比喻：外邦人如同被接上的「野橄欖枝」，不可驕傲。",
        "最終目標：在基督裡，猶太人與外邦人將合而為一。"
    ]),
    ("信仰的實踐：處理具體生活衝突 (12-16章)", [
        "處理與群體緊張相關的問題：",
        "    - 飲食問題 (14章): 祭偶像之物。",
        "    - 守日問題 (14章): 守安息日。",
        "保羅的勸勉：",
        "    - 不要互相論斷，要彼此接納。"
    ]),
    ("羅馬書的核心概念：上帝的「義」", [
        "貫穿全書的關鍵字：上帝 (153次)、律法 (72次)、基督 (65次)。",
        "核心主題：「義」 (Righteousness)",
        "    - 外邦人的問題：不義 (Unrighteousness)",
        "    - 猶太人的問題：自以為義 (Self-righteousness)",
        "提醒：「好人比壞人更難進天國」，因為自義是最大障礙。"
    ]),
    ("救贖的三部曲", [
        "1. 稱義 (Justification) - 罪的刑罰",
        "    - 意義：「上帝說你沒事了！」",
        "2. 成聖 (Sanctification) - 罪的能力",
        "    - 意義：脫離罪的權勢，一個持續的過程。",
        "3. 得榮耀 (Glorification) - 罪的存在",
        "    - 意義：未來完全脫離罪，身體得贖。"
    ]),
    ("全書大綱總覽", [
        "按主題劃分：",
        "    - 信心 (1-4章)",
        "    - 盼望 (5-11章)",
        "    - 愛 (12-16章)",
        "詳細架構：",
        "    - 福音的義 (1-8章)",
        "    - 神對以色列的計畫 (9-11章)",
        "    - 基督徒的生活 (12-16章)"
    ]),
    ("結論與今日應用", [
        "核心信息：拆毀教會內部分隔的牆，促進在基督裡的合一。",
        "福音的大能：改變生命、更新社會、使人和睦。",
        "個人反思：",
        "    - 我是否因自己的善行而自以為義？",
        "    - 我如何對待與我背景不同的弟兄姊妹？"
    ]),
    ("提問與交流", [
        "Q & A"
    ])
]

try:
    # --- Load the template presentation ---
    TEMPLATE_PATH = Path(__file__).resolve().with_name("template.pptx")
    prs = Presentation(TEMPLATE_PATH)


    # --- Identify the target slide layout ---
    # Python uses 0-based indexing, so we subtract 1
    template_slide = prs.slides[TEMPLATE_SLIDE_NUMBER - 1]
    target_layout = template_slide.slide_layout

    # --- Create Title Slide (uses the default title layout, usually index 0) ---
    title_slide_layout = prs.slide_layouts[0]
    slide = prs.slides.add_slide(title_slide_layout)
    title = slide.shapes.title
    subtitle = slide.placeholders[1]
    title.text = slides_content[0][1][0]
    subtitle.text = "\n".join(slides_content[0][1][1:])

    # --- Create Content Slides using the target layout ---
    for item in slides_content[1:]:
        slide = prs.slides.add_slide(target_layout)
        
        # Set title
        # It's safer to check if a title placeholder exists
        if slide.shapes.title:
            slide.shapes.title.text = item[0]
        
        # Find the main body/content placeholder and populate it
        # This loop makes the code more robust if the body placeholder isn't the first one
        for shape in slide.placeholders:
            if not getattr(shape, "text_frame", None):
                continue
            if not shape.is_placeholder:
                continue
            if ALLOWED_PLACEHOLDER_TYPES and shape.placeholder_format.type not in ALLOWED_PLACEHOLDER_TYPES:
                continue

            tf = shape.text_frame
            tf.clear()
            numbering_counters = {}
            for idx, raw_point in enumerate(item[1]):
                paragraph = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
                stripped_point = raw_point.lstrip(' ')
                indent_spaces = len(raw_point) - len(stripped_point)
                level = max(indent_spaces // 4, 0)

                # Drop numbering context for deeper levels when we move up
                for tracked_level in list(numbering_counters.keys()):
                    if tracked_level > level:
                        numbering_counters.pop(tracked_level, None)

                number_match = NUMBERED_POINT_PATTERN.match(stripped_point)
                if number_match:
                    text = number_match.group(2)
                    target_number = int(number_match.group(1))
                    current_expected = numbering_counters.get(level)
                    start_at = target_number if current_expected is None or target_number != current_expected else None
                    _apply_numbered_paragraph(paragraph, level, start_at)
                    numbering_counters[level] = target_number + 1
                else:
                    is_dash_bullet = stripped_point.startswith("- ")
                    text = stripped_point[2:] if is_dash_bullet else stripped_point

                    # Treat empty strings as plain paragraphs without bullets
                    if text.strip():
                        _apply_bullet_paragraph(paragraph, max(level, 0))
                        numbering_counters.pop(level, None)
                    else:
                        _apply_plain_paragraph(paragraph)
                        numbering_counters.pop(level, None)

                paragraph.text = text
                for run in paragraph.runs:
                    run.font.size = Pt(24)
            break  # Stop after finding and filling the first body placeholder

    # Remove the first two slides from the template since they're layout examples
    for _ in range(min(2, len(prs.slides))):
        _remove_slide(prs, 0)

    # --- Save the new presentation ---
    output_path = TEMPLATE_PATH.parent / "Romans_Generated.pptx"
    prs.save(output_path)

    print(f"Success! New presentation saved as '{output_path.name}'")
    print(f"It uses the layout from slide #{TEMPLATE_SLIDE_NUMBER} of your template.")

except FileNotFoundError:
    print("\nERROR: 'template.pptx' not found.")
    print("Please make sure your template file is in the same folder as the script and is named correctly.")
except IndexError:
    print(f"\nERROR: Slide number {TEMPLATE_SLIDE_NUMBER} does not exist in your template.")
    print("Please check the total number of slides in 'template.pptx' and update the TEMPLATE_SLIDE_NUMBER variable.")
