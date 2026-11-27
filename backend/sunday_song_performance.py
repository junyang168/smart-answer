"""Apply explicit per-service verse/repeat directions without editing the catalog."""
import re


def apply_song_notes(sections, notes):
    if not notes or not notes.strip():
        return list(sections)
    selection = re.search(r'(?:只唱|選唱|选唱)\s*第?\s*([一二三四五六七八九十\d、，,及和\s]+)\s*[節节]', notes)
    repeats = bool(re.search(r'(?:最後|最后|末段|末[節节]).{0,12}(?:重複|重复|再唱|返覆).{0,8}(?:一次|1次|一遍|1遍|一[張张])|(?:重複|重复)\s*(?:最後|最后|末)[一]?\s*[節节段].{0,8}(?:一次|1次|一遍|1遍)', notes))
    # A line repeat is local to this Sunday's arrangement. Preserve stanza
    # boundaries and catalog text; never interpret an absent count as one.
    prefix = re.fullmatch(r'\s*(?:(每[段節节])|(第一[段節节]))?\s*(?:前|開頭|开头)([一二三四五六七八九\d]+)行\s*(?:重複|重复|返覆|反覆)\s*([一二三四\d]+)[次遍]\s*[。.]?\s*', notes)
    if prefix:
        numeral = dict(zip('一二三四五六七八九', range(1, 10)))
        def number(value):
            return int(value) if value.isdigit() else numeral.get(value, 0)
        count, times = number(prefix[3]), number(prefix[4])
        targets = list(range(len(sections))) if prefix[1] else [0]
        if not 1 <= times <= 4 or count < 1 or not sections or any(count > len(sections[i]) for i in targets):
            raise ValueError('前三行重複安排無法對應現有歌詞，請核對行數、段落與重複次數')
        return [section[:count] * (times + 1) + section[count:] if i in targets else list(section)
                for i, section in enumerate(sections)]
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
    if re.search(r'重複|重复|返覆|反覆|再唱', notes) and not repeats:
        raise ValueError('重複安排沒有明確次數，請問最後一節是否再唱一次？')
    return result
