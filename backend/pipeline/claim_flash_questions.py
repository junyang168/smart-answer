"""Flash Jev wording in one place; judgments here, routing in the callers.

Guidance: https://github.com/typesafe-ai/skills/blob/main/skills/typesafe-ai/SKILL.md
Question ids are invisible to Jev. Each instruction names its own subject.
"""

CONTRACT = 'claim_flash_classification_v2'
LOOKUP_UNCERTAINTY_FLOOR = .25  # Existing routing policy; not tuned by this change.

FIDELITY = {'faithful':'實質意思有來源支持，沒有改義。',
            'unsupported':'增添原文沒有的內容，但沒有改變原有判斷。',
            'changed':'改變條件、範圍、程度、模態或因果；兼有添加也選此項。',
            'contradicted':'與來源明確相反；兼有其他問題也選此項。','uncertain':'材料不足或含混。'}
SUPPORT = {'supported':'所有實質成分有據。','partial':'部分成分有據，另有成分無據。',
           'unsupported':'實質成分均缺少支持。','contradicted':'有明確相反的原文；兼有缺據也選此項。',
           'uncertain':'材料不足，無法判定。'}
SCRIPTURE = {'complete':'關鍵經文及其論證作用完整、對應正確。','missing':'有遺漏，沒有錯配。',
             'mismatched':'有錯配，沒有遺漏。','missing_and_mismatched':'同時有遺漏與錯配。','uncertain':'無法判定。'}
ROLES = {
    'speaker': ('這項內容的發言來源角色是誰？',
                {'professor':'教授自己的講解或判斷，不是單純引用。','audience':'聽眾自己的發言，不是單純朗讀引文。',
                 'quoted_source':'僅承載經文或第三者引語。','mixed':'混合多種來源，不能歸一類。','uncertain':'原文不能確定。'}),
    'stance': ('教授對這個完整判斷採取什麼態度？',
               {'asserted':'教授採納或肯定該完整判斷，包括判斷自身的否定。','opposed':'教授明確反對該完整判斷。',
                'questioned':'教授設問，材料中尚未回答。','quoted':'僅轉述或假設，未表明採納或反對。',
                'uncertain':'材料不足或含混。'}),
    'questioner': ('這個問題由誰提出？',
                   {'professor':'教授設問。','audience':'聽眾提問。','quoted_source':'引述別人的問題。',
                    'mixed':'多方共同提出，不能歸一位。','uncertain':'無法確定。'})}


def choice(instruction, criteria):
    return {'type':'choice','instructions':instruction,'criteria':criteria}


def record_fidelity():
    return choice('這項記錄的實質內容與完整原文清單的對應情況是哪種？問題記錄只核對提問本身。',FIDELITY)


def decomposition():
    return choice('`components` 對 `record.statement` 的保留情況是哪種？條件描述不聲稱行為已發生。',
                  {'faithful':'完整保留原意。','omitted':'有遺漏，沒有添加或改義。','added':'有添加，沒有遺漏或改義。',
                   'changed':'有改義或多種缺陷。','uncertain':'無法判定。'})


def role(field, text, *, speaker=None):
    instruction,criteria=ROLES[field]
    if field=='stance' and speaker and speaker!='professor':
        # The actor is a query parameter, not the answer being checked. It is
        # supplied only to this question; the speaker question remains blind.
        actor={'audience':'聽眾','quoted_source':'所引用的來源'}.get(speaker,'相應發言者')
        instruction=instruction.replace('教授',actor)
        criteria={k:v.replace('教授',actor) for k,v in criteria.items()}
    return choice('只根據原文，'+instruction+'\n內容：'+text,criteria)


def component(item):
    context='這是原句的條件描述，不聲稱特定行為已發生。' if item['role']=='embedded_description' else ''
    return choice('完整 `original_text_excerpts` 對下列判斷的支持程度是哪種？'+context+
                  '保留原有條件與語氣。`source_context` 只消歧，不補未掛載的論據。\n判斷：'+item['text'],SUPPORT)


def claim():
    return {
        'statement_fidelity':choice('`claim.statement` 與教授原意的對應情況是哪種？保留原有條件、否定、對象與歸屬。',FIDELITY),
        'support':choice('完整 `evidence` 合起來對 `claim.statement` 的支持程度是哪種？單項可只支持部分。',SUPPORT),
        'scripture':choice('教授用於本主張的關鍵經文及其作用，在目前記錄中是哪種情況？不要求抄入 statement。',SCRIPTURE)}


def coverage():
    return {
        'missing_lower':choice('這段原文的實質論據和觀察，由 `records` 覆蓋的情況是哪種？忠實概括視為覆蓋，口頭強調不算論據。',
            {'covered':'實質內容已有承載。','missing_evidence':'只漏論據或步驟。','missing_observation':'只漏觀察。',
             'missing_both':'論據與觀察均漏。','uncertain':'無法判定。'}),
        'conclusion_candidate':choice('這段原文包含哪類實質判斷候選？不判斷有無獨立支持，不決定是否建立 Claim；已有連接不排除候選。',
            {'substantive_judgment':'有值得 GPT 核對保存方式的實質判斷，即使兼有背景或設問。',
             'background_or_example':'只有故事細節、背景或例子。','oral_or_repetition':'只有口頭強調或重複。',
             'question_only':'只有未回答的設問。','uncertain':'無法判定。'})}


def answer_links():
    return choice('`question` 與所列 `claims` 的答案連線是哪種情況？用 `source_context` 消歧。'
        '保留有效直接回答及據此形成的結論，一般方法不當作具體答案。'
        '沒有合適的 Claim 時空答案清單也可完整正確，不因未建 Claim 就判漏接；沒有現有連線也不能判錯接。',
        {'complete':'完整正確。','missing':'只有漏接。','mismatched':'只有答非所問的錯接。',
         'missing_and_mismatched':'同時有漏接與錯接。','uncertain':'無法判定。'})


def relation(options):
    return choice('教授原文中 `A` 與 `B` 的直接論證關係是哪種？兩端已驗證，只判關係。'
                  '不要求單項證明整個 Claim，僅同主題不算。既有關係成立就保留其類型方向。',options)


def reason():
    return choice('`existing_relation.reason` 對其現有端點、類型與方向的說明是哪種情況？',
        {'grounded':'理由每項有據且一致。','unsupported':'含無據成分，但沒有錯配。',
         'mismatched':'錯配端點、類型或方向；兼有無據也選此項。','uncertain':'無法判定。'})


LOOKUP_RULE = '只找已有節點的直接論證關係，不重審內容。'
MATCH_RULE = ('此記錄可支持、解釋、限定或反駁 Claim 的一部分。'
              '不要求單項證明整個主張，僅同主題或相同關鍵字不算。')


def lookup(label, excluded, remaining):
    scope=f'只查 `records.{label}` 與未排除的 `claims`。已排除的 Claim：'+','.join(excluded)+'。'
    return {
        'match':{'type':'noul','instructions':scope+'至少有一個直接論證匹配嗎？'+MATCH_RULE+LOOKUP_RULE},
        'claim':choice(scope+'最直接對應哪個 Claim？沒有則選 none。'+MATCH_RULE+LOOKUP_RULE,
                       {**{c:f'`claims.{c}` 中的主張。' for c in remaining},'none':'沒有直接論證匹配。'})}


def source_location(label, remaining):
    return choice(f'只查 `records.{label}`。在所列選項中，哪個 `current_paragraphs` 段落最直接承載'
        '待查內容或其一個實質成分？可依歷史引文找新版對應，但歷史引文不是目前支持。'
        '只有同話題、設問未回答或口頭強調不算支持。只從這次所列段落選，其他段落已選過或不在本批。'
        '可選補充支持的另一段，不要求單段證明整項。',
        {**{pk: f'`current_paragraphs.{pk}` 的完整原文。' for pk in remaining},
         'none': '本批剩餘段落沒有直接對應或補充支持。', 'uncertain': '無法判定本批剩餘段落。'})
