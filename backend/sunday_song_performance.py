"""Apply explicit per-service verse/repeat directions without editing the catalog."""
import re


def apply_song_notes(sections, notes):
    if not notes or not notes.strip():
        return list(sections)
    selection = re.search(r'(?:只唱|選唱|选唱)\s*第?\s*([一二三四五六七八九十\d、，,及和\s]+)\s*[節节]', notes)
    repeats = bool(re.search(r'(?:最後|最后|末段|末[節节]).{0,12}(?:重複|重复|再唱|返覆).{0,8}(?:一次|1次|一遍|1遍|一[張张])|(?:重複|重复)\s*(?:最後|最后|末)[一]?\s*[節节段].{0,8}(?:一次|1次|一遍|1遍)', notes))
    if not selection and not repeats:
        raise ValueError('演唱說明尚未明確到可自動排版，請提供選唱節次或最後一節重複次數')
    result = list(sections)
    if selection:
        numeral = dict(zip('一二三四五六七八九十',range(1,11)))
        order = [int(token) if token.isdigit() else numeral[token] for token in re.findall(r'\d+|[一二三四五六七八九十]', selection.group(1))]
        if not order or any(index < 1 or index > len(sections) for index in order):
            raise ValueError('指定的詩節無法對應現有歌詞段落，請核對歌詞分段')
        result = [sections[index-1] for index in order]
    if repeats:
        if not result:
            raise ValueError('沒有歌詞可重複，請先匯入完整歌詞')
        result.append(result[-1])
    # A selection plus an additional unspecified repeat must not silently lose
    # the extra direction merely because the selection itself was recognized.
    if re.search(r'重複|重复|返覆|再唱', notes) and not repeats:
        raise ValueError('重複安排沒有明確次數，請問最後一節是否再唱一次？')
    return result
