#!/usr/bin/env python3
"""文庫獨立完整性與正確性審計。

平台已經有十幾套自檢——`sentence_ledger`、`base_contract_coverage`、
`observation_argument_coverage`、`ViewpointResolutionLedger`、Program Audit、
`ViewpointQualityReport`。它們全在回答同一類問題：**有沒有漏、引用解不解析得
開**；而且它們由流水線自己實現，共用同一套讀取器、同一批不變量、同一份資料模
型。流水線的假設錯了，它自己的檢查跟著錯。

這支程式回答的是另一個問題：**對不對**。它獨立的意思有三條，缺一不可：

1. **自己的程式碼路徑**——不 import 任何 `backend/` 模組，不復用流水線的讀取器
   與模型。資料庫用 `psql` 子行程讀，模型用 `urllib` 直接呼叫 HTTP 端點；連驅動
   程式和 SDK 的設定都不與流水線共用。
2. **直接讀原件**——不信 `anchor_state`，不信存下來的 `paragraph_text_sha256`
   與 `verbatim_excerpt_sha256`，直接打開磁碟上的逐字稿核對。存下來的雜湊只當成
   **待查的宣稱**，不當成證據。
3. **不同的模型**——需要判斷的部分（第 3、4 層），用沒參與過原判定的模型。提議
   是 `gpt-5.6-sol`，複核是 `claude-opus-5`，所以審計用第三家的 Gemini。

四層：

| 層 | 查什麼 | 方式 |
| --- | --- | --- |
| 1 逐字對得上 | 片段的 `verbatim_excerpt` 在原件裡真的存在於所記位置 | 確定性 |
| 2 覆蓋誠實 | 聲稱覆蓋的來源與實際用到的對得上；已批准內容的依賴全部可解析 | 確定性 |
| 3 主張站得住 | 這條主張能否從它所引的證據推出 | 抽樣 + 獨立模型 |
| 4 觀點歸併對 | 判為同一觀點的主張，真值條件是否真的一致 | 抽樣 + 獨立模型 |

輸出是**比率**，不是清單，可隨時間比較。有異議的進人工佇列，與 Solution
Architecture D4 第 4 步同一個出口。

只讀。不寫入 PostgreSQL，不修改任何記錄。

    scripts/audit-library.py
    scripts/audit-library.py --layers 1,2          # 只跑確定性的兩層，不呼叫模型
    scripts/audit-library.py --claims 40 --viewpoints 15 --seed 241
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import ssl
import subprocess
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parent.parent

#: 被視為「已批准」的 `review_status`。第 2 層只對這些物件要求依賴全部可解析：
#: candidate 還在流程裡，引用暫時解不開不是缺陷。
APPROVED_STATUSES = {"approved", "system_approved", "human_approved"}

#: 只有「等價」才算觀點的成員。`supports`／`extends`／`qualifies`／`applies` 是
#: 關係，不是身份——見 CanonicalViewpoint 設計第 4 節。第 4 層問「這幾條是不是
#: 同一個觀點」，只能拿成員來問；拿一條 `qualifies` 去問，模型當然說不是同一個，
#: 而那是審計自己問錯了。
MEMBER_LINK_TYPES = {"equivalent_full", "equivalent_component"}

#: 一條欄位路徑要有這個比例的值解析得開，才當它是指向物件的引用。低於這條線的
#: 是撞號，不是引用——見 `audit_coverage` 裡的說明。
REFERENCE_PATH_MIN_RATE = 0.9

#: 這個庫的物件 id 一律是「前綴 + 連字號 + 其餘」：`DK-91b546f25db1-P03-CL013`、
#: `CV-01f185ea9e965baab351`、`CP-COVENANT-LAW-CORE-NINE-01-S-123b74723555`。不長
#: 這樣的值不是物件引用，比對之前先擋掉。
#:
#: 擋掉的是這幾類，全都真的出現在 `*_id` 欄位裡：來源內部的錨點編號（`E037`，沒
#: 有連字號）、經文出處（`馬太福音16:28`）、逐字稿標題（`2016 NYSC 專題…`，有空
#: 白）。其中 `E037` 這類最麻煩：庫裡有 140 個舊的 evidence_step 物件 id 剛好也長
#: 這樣，於是同一批值有些「解析得開」，逐字稿裡的另外 151 個就被報成斷掉的依賴。
OBJECT_ID_SHAPE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-[!-~]+$")

#: 逐字稿目錄的優先順序。`source_documents.source_path` 缺失時（早期的 `SRC-L3`
#: 一類記錄只留了 `transcript_id`）按這個順序找檔案。
TRANSCRIPT_DIRS = ("script_published", "script_review", "script_patched")

#: 校對者刪字的方式是劃掉，不是刪掉，所以刪除是可逆的。兩個標記之間的內容應讀作
#: **不存在**。這條規則屬於原件本身的體例（校對者在編輯器裡看到的就是刪除線），
#: 不是流水線的假設，所以審計照樣要遵守：一條只存在於刪除線裡的引文，不是教授現
#: 在這份逐字稿裡說的話。
#:
#: `[^~]+?` 表示落單的標記什麼都不刪——與其猜它想刪到哪裡，不如不刪。
STRIKETHROUGH = re.compile(r"~~([^~]+?)~~", re.S)

#: 逐字比對失敗時唯一允許的放寬：把連續的省略號收成一個、把連續空白收成一個。
#:
#: 這條規則是量出來的，不是猜的。SRC-L3 的四條片段全部只差在這裡——原文是
#: `使各國、各方……各國、各族`（兩個 U+2026），片段存成 `使各國、各方…各國、各族`
#: （一個）。那不是編造，是抄寫時的正規化。但它也**不是逐字**，所以它自成一類
#: `punctuation_only`，單獨計數、單獨報，不併進通過數裡。
ELLIPSIS_RUN = re.compile(r"[…]{2,}")
WHITESPACE_RUN = re.compile(r"\s+")


def normalized(text: str) -> str:
    return WHITESPACE_RUN.sub(" ", ELLIPSIS_RUN.sub("…", text or "")).strip()


DEFAULT_MODEL = "gemini-3.7-flash"
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


# ---------------------------------------------------------------------------
# 設定：環境變數優先，其次是 repo 根目錄的 .env
# ---------------------------------------------------------------------------


def load_settings() -> dict[str, str]:
    """讀取設定。不 import 任何專案模組，自己解析 `.env`。"""

    values: dict[str, str] = {}
    env_file = REPO_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^([A-Z_0-9]+)=(.*)$", line.strip())
            if match:
                values[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    for key in ("KNOWLEDGE_DATABASE_URL", "DATA_BASE_DIR", "GEMINI_API_KEY1"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


# ---------------------------------------------------------------------------
# 資料庫：psql 子行程，不用任何 ORM 或專案的 store
# ---------------------------------------------------------------------------


class Store:
    """把 authoring store 的物件當成 JSON 行讀進來。

    刻意不用 psycopg 或專案的 `PostgresKnowledgeStore`：審計要有自己的讀取路徑，
    連「一個物件長什麼樣」都從 `payload` 現讀，不經過 Pydantic 模型。
    """

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._cache: dict[str, list[dict[str, Any]]] = {}

    def _query(self, sql: str) -> list[dict[str, Any]]:
        result = subprocess.run(
            ["psql", self.database_url, "-At", "-v", "ON_ERROR_STOP=1", "-c", sql],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise SystemExit(f"psql failed: {result.stderr.strip()}")
        return [json.loads(line) for line in result.stdout.splitlines() if line]

    def collection(self, name: str) -> list[dict[str, Any]]:
        """一個 collection 的當前物件（不含已 retire 的）。"""

        if name not in self._cache:
            self._cache[name] = self._query(
                "select json_build_object("
                "'object_id', object_id, 'review_status', review_status,"
                "'revision', revision, 'payload', payload)::text "
                f"from wang_knowledge.objects where collection = '{name}' "
                "and retired_at is null order by object_id"
            )
        return self._cache[name]

    def collection_names(self) -> list[str]:
        rows = self._query(
            "select json_build_object('collection', collection)::text "
            "from wang_knowledge.objects group by collection order by collection"
        )
        return [row["collection"] for row in rows]

    def retired_ids(self) -> set[tuple[str, str]]:
        rows = self._query(
            "select json_build_object('c', collection, 'i', object_id)::text "
            "from wang_knowledge.objects where retired_at is not null"
        )
        return {(row["c"], row["i"]) for row in rows}


# ---------------------------------------------------------------------------
# 原件：磁碟上的逐字稿與母本
# ---------------------------------------------------------------------------


def live_text(text: str) -> str:
    """一段文字去掉被校對者劃掉的部分。

    刪掉的地方換成換行，不是換成空字串。`甲~~乙~~丙` 收攏成 `甲丙` 會造出教授從
    來沒說過的一句話，而逐字核對會高高興興地認為它連續存在於原文裡。
    """

    return STRIKETHROUGH.sub("\n", str(text or ""))


class SourceFile:
    """磁碟上一份原件，以及審計自己算出來的雜湊與段落。"""

    def __init__(self, path: Path, source_type: str) -> None:
        self.path = path
        self.source_type = source_type
        raw = path.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        if source_type == "notes_manuscript":
            self.segments = self._markdown_segments(raw)
        else:
            self.segments = self._transcript_segments(raw)
        self.live_whole = "\n".join(live_text(s["text"]) for s in self.segments)
        self.raw_whole = "\n".join(s["text"] for s in self.segments)
        self._by_index: dict[str, dict[str, Any]] = {}
        for segment in self.segments:
            if segment.get("index") is not None:
                self._by_index.setdefault(str(segment["index"]), segment)

    @staticmethod
    def _transcript_segments(raw: bytes) -> list[dict[str, Any]]:
        parsed = json.loads(raw)
        script = parsed["script"] if isinstance(parsed, dict) else parsed
        rows = []
        for item in script or []:
            row = dict(item) if isinstance(item, dict) else {"text": str(item or "")}
            row["text"] = str(row.get("text") or "")
            rows.append(row)
        return rows

    @staticmethod
    def _markdown_segments(raw: bytes) -> list[dict[str, Any]]:
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        blocks = [b.strip() for b in re.split(r"\n[ \t]*\n+", text) if b.strip()]
        return [{"text": block, "index": i + 1} for i, block in enumerate(blocks)]

    def by_ordinal(self, ordinal: int) -> dict[str, Any] | None:
        """第 n 段（1-based）。`paragraph_key` 的 `S0016` 就是這個 n。"""

        if 1 <= ordinal <= len(self.segments):
            return self.segments[ordinal - 1]
        return None

    def by_index(self, index: Any) -> dict[str, Any] | None:
        """`index` 欄位等於這個值的那一段，也就是 `source_segment_index`。"""

        return self._by_index.get(str(index))


class SourceIndex:
    """`source_id` → 磁碟上的原件。"""

    def __init__(self, documents: list[dict[str, Any]], data_base_dir: Path) -> None:
        self.data_base_dir = data_base_dir
        self.documents = {row["object_id"]: row["payload"] for row in documents}
        self._files: dict[str, SourceFile | None] = {}
        self.unresolved: dict[str, str] = {}

    def path_for(self, payload: dict[str, Any]) -> Path | None:
        declared = payload.get("source_path")
        if declared and Path(str(declared)).is_file():
            return Path(str(declared))
        transcript_id = str(payload.get("transcript_id") or "").strip()
        if transcript_id:
            for directory in TRANSCRIPT_DIRS:
                candidate = self.data_base_dir / directory / f"{transcript_id}.json"
                if candidate.is_file():
                    return candidate
        return None

    def file_for(self, source_id: str) -> SourceFile | None:
        if source_id in self._files:
            return self._files[source_id]
        payload = self.documents.get(source_id)
        if payload is None:
            self.unresolved[source_id] = "no_source_document"
            self._files[source_id] = None
            return None
        path = self.path_for(payload)
        if path is None:
            self.unresolved[source_id] = "no_file_on_disk"
            self._files[source_id] = None
            return None
        try:
            source_file = SourceFile(path, str(payload.get("source_type") or "sermon_transcript"))
        except Exception as error:  # 壞掉的原件本身就是審計結果，不是崩潰的理由
            self.unresolved[source_id] = f"unreadable: {error}"
            self._files[source_id] = None
            return None
        self._files[source_id] = source_file
        return source_file


# ---------------------------------------------------------------------------
# 第 1 層：逐字對得上
# ---------------------------------------------------------------------------


def audit_verbatim(store: Store, sources: SourceIndex) -> dict[str, Any]:
    """每一條 `source_fragment` 的引文，在原件裡真的存在於所記位置嗎。

    片段記了兩個各自獨立的位置：`paragraph_key`（`S0016` 是第 16 段）和
    `source_segment_index`（逐字稿裡那一段自己的 `index` 欄位）。兩個都查，因為
    兩個都可能單獨過期——五份來源在片段綁定之後重新校對過，段落數變了，`S` 序號
    跟著失效，而 `index` 欄位還指得對。

    片段自己帶的 `source_sha256` 只當成**宣稱**：拿它跟現在磁碟上這份檔案的雜湊
    比，不一致就是這條錨點綁在一個已經不存在的版本上——`anchor_state` 仍然寫著
    `source_version_bound`，但那是它被寫入當時的事。
    """

    fragments = store.collection("source_fragments")
    counts: Counter[str] = Counter()
    findings: list[dict[str, Any]] = []
    binding: Counter[str] = Counter()
    stale_by_source: Counter[str] = Counter()
    #: 審計的判定 × 片段自己宣告的 `anchor_state`。兩邊不一致的格子，正是流水線
    #: 自檢看不見的地方。
    declared: Counter[tuple[str, str]] = Counter()

    def record(fragment_id: str, source_id: str, verdict: str, payload: dict[str, Any], **extra):
        counts[verdict] += 1
        declared[(verdict, str(payload.get("anchor_state") or "—"))] += 1
        if verdict not in ("pass",):
            findings.append({
                "fragment_id": fragment_id,
                "source_id": source_id,
                "verdict": verdict,
                "anchor_state": payload.get("anchor_state"),
                **extra,
            })

    for row in fragments:
        payload = row["payload"]
        fragment_id = row["object_id"]
        source_id = str(payload.get("source_id") or "")
        source_file = sources.file_for(source_id)
        if source_file is None:
            binding["no_source_file"] += 1
            record(
                fragment_id, source_id, "no_source_file", payload,
                detail=sources.unresolved.get(source_id, "unknown"),
            )
            continue

        # 宣稱的版本 vs 磁碟上的版本。三種狀態要分開，因為它們是三件不同的事：
        # 綁在當前版本、綁在一個已經不存在的版本、以及**根本沒綁**——最後這種
        # 讀起來最無害，其實是連宣稱都沒有。
        claimed_sha = str(payload.get("source_sha256") or "")
        if not claimed_sha:
            version_state = "unclaimed"
        elif claimed_sha == source_file.sha256:
            version_state = "current"
        else:
            version_state = "stale"
            stale_by_source[source_id] += 1
        binding[version_state] += 1

        excerpt = str(payload.get("verbatim_excerpt") or "")
        if not excerpt.strip():
            record(
                fragment_id, source_id, "no_excerpt", payload,
                detail="片段沒有 verbatim_excerpt，無從核對",
                version_state=version_state,
            )
            continue

        located_by: list[str] = []
        paragraph_key = str(payload.get("paragraph_key") or "")
        ordinal_match = re.match(r"^S(\d+)$", paragraph_key)
        segment = None
        if ordinal_match:
            segment = source_file.by_ordinal(int(ordinal_match.group(1)))
        elif paragraph_key:
            segment = source_file.by_index(paragraph_key)
        if segment is not None and excerpt in live_text(segment.get("text", "")):
            located_by.append("paragraph_key")

        segment_index = payload.get("source_segment_index")
        by_index = source_file.by_index(segment_index) if segment_index is not None else None
        if by_index is not None and excerpt in live_text(by_index.get("text", "")):
            located_by.append("source_segment_index")

        if located_by:
            record(fragment_id, source_id, "pass", payload)
            if len(located_by) == 1 and segment_index is not None and paragraph_key:
                counts["one_locator_only"] += 1
                findings.append({
                    "fragment_id": fragment_id,
                    "source_id": source_id,
                    "verdict": "one_locator_only",
                    "detail": f"只有 {located_by[0]} 指得對，另一個定位器指到別處",
                    "version_state": version_state,
                })
            continue

        # 引文不在任何一個所記位置。那它到底還在不在這份原件裡？
        located_text = None
        if segment is not None:
            located_text = live_text(segment.get("text", ""))
        elif by_index is not None:
            located_text = live_text(by_index.get("text", ""))

        if excerpt in source_file.live_whole:
            verdict, detail = "misplaced", "引文在原件裡，但不在任何一個所記位置"
        elif excerpt in source_file.raw_whole:
            verdict, detail = "deleted_text_only", "引文只存在於校對者劃掉的文字裡"
        elif normalized(excerpt) in normalized(source_file.live_whole):
            verdict = "punctuation_only"
            detail = (
                "引文與原文只差在省略號或空白的寫法上；教授確實說過這句話，"
                "但存下來的字串不是逐字的"
            )
            if located_text is not None and normalized(excerpt) in normalized(located_text):
                detail += "，位置正確"
        else:
            verdict, detail = "absent", "引文不在這份原件裡"
        record(
            fragment_id, source_id, verdict, payload,
            detail=detail,
            paragraph_key=paragraph_key or None,
            source_segment_index=segment_index,
            version_state=version_state,
            excerpt=excerpt[:120],
        )

    return {
        "layer": 1,
        "name": "逐字對得上",
        "total": len(fragments),
        "passed": counts["pass"],
        "counts": dict(counts),
        "version_binding": {**binding, "stale_by_source": dict(stale_by_source)},
        "verdict_by_declared_anchor_state": {
            f"{verdict} / {state}": count for (verdict, state), count in sorted(declared.items())
        },
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# 第 2 層：覆蓋誠實
# ---------------------------------------------------------------------------


ID_KEY = re.compile(r"(^|_)(id|ids|ref|refs)$")


def walk_references(payload: Any, own_ids: set[str], path: str = "") -> Iterable[tuple[str, str]]:
    """payload 裡每一個看起來像「指向另一個物件」的字串，連同它的欄位名。

    自己的 ID 不算引用（`viewpoint_id` 出現在 `canonical_viewpoints` 上是身分，
    出現在 `viewpoint_claim_links` 上才是引用），所以要把 `own_ids` 排掉。
    """

    if isinstance(payload, dict):
        for key, value in payload.items():
            yield from walk_references(value, own_ids, f"{path}.{key}" if path else key)
    elif isinstance(payload, list):
        for item in payload:
            yield from walk_references(item, own_ids, path)
    elif isinstance(payload, str):
        field = path.rsplit(".", 1)[-1]
        if ID_KEY.search(field) and payload and payload not in own_ids:
            # 回傳完整路徑，不是最後一段。`evidence_step_ids` 指的是庫裡的物件，
            # 而 `occurrences.anchors.evidence_id` 是來源內部的編號（`E037`），
            # 兩者最後一段像、意思不同。只看最後一段時，少數 `E037` 恰好撞上某個
            # 物件 id，就足以把整個欄位誤判成引用欄位，於是另外 184 個本地編號被
            # 報成斷掉的依賴。
            # 雜湊不是 ID：64 位十六進位一律跳過，否則 `*_sha256` 欄位會把整份
            # 報告淹掉。
            if not re.fullmatch(r"[0-9a-f]{64}", payload) and OBJECT_ID_SHAPE.match(payload):
                yield path, payload


def audit_coverage(store: Store, sources: SourceIndex) -> dict[str, Any]:
    """已批准的內容，依賴解不解析得開；聲稱覆蓋的來源與實際用到的對不對得上。

    參照解析刻意**不查表**。硬寫一張 `claim_id → claims` 的對照表，等於把流水線
    的資料模型抄一份進審計，模型錯了審計跟著錯。改成先建全庫 ID 索引，再按**欄位
    的完整路徑**看它的值解不解析得開：一條路徑底下的值**一個都解不開**
    （`occurrence_ref` 是這樣），那它根本不指向任何物件，報成「查無此物的 ID」而
    不是失敗；只有**部分**解不開的，才是真的斷掉的依賴。
    """

    collections = store.collection_names()
    universe: dict[str, set[str]] = defaultdict(set)
    own_ids_by_object: dict[tuple[str, str], set[str]] = {}
    for name in collections:
        for row in store.collection(name):
            universe[row["object_id"]].add(name)
            own = {row["object_id"]}
            for key, value in row["payload"].items():
                if isinstance(value, str) and ID_KEY.search(key) and value == row["object_id"]:
                    own.add(value)
            own_ids_by_object[(name, row["object_id"])] = own
    retired_ids = {object_id for _, object_id in store.retired_ids()}

    resolved_by_field: Counter[str] = Counter()
    unresolved_by_field: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    checked_total = 0
    approved_total = 0
    approved_ids: set[tuple[str, str]] = set()
    objects_with_gap: set[tuple[str, str]] = set()
    retired_targets: list[dict[str, Any]] = []

    # 查全部當前物件，不只查 `review_status` 說已批准的那些。
    #
    # 卡片原話是「已批准內容的依賴全部可解析」，照字面做出來的範圍是 360 個物
    # 件，而且全在觀點層——claim 層一條都沒查到。原因不是 claim 層沒審過：1,530
    # 條 claim 各有一條 AI 裁定，只是從來沒有回寫進 `review_status`（見 #243）。
    # 拿一個還沒定下來的詞彙去圈審計範圍，量到的是詞彙，不是文庫。
    for name in collections:
        for row in store.collection(name):
            checked_total += 1
            if row["review_status"] in APPROVED_STATUSES:
                approved_total += 1
                approved_ids.add((name, row["object_id"]))
            own = own_ids_by_object[(name, row["object_id"])]
            for field, value in walk_references(row["payload"], own):
                if value in universe:
                    resolved_by_field[field] += 1
                elif value in retired_ids:
                    # 指向一個已經 retire 的物件。這不是斷掉的依賴——那條記錄還
                    # 在庫裡，只是不再是當前版本——但它也不是乾淨的引用，所以自
                    # 成一類。598 條 claim 引用落在這裡。
                    resolved_by_field[field] += 1
                    retired_targets.append({
                        "collection": name,
                        "object_id": row["object_id"],
                        "field": field,
                        "value": value,
                    })
                else:
                    unresolved_by_field[field].append({
                        "collection": name,
                        "object_id": row["object_id"],
                        "field": field,
                        "value": value,
                    })

    dangling: list[dict[str, Any]] = []
    ids_with_no_collection: list[dict[str, Any]] = []
    ambiguous_paths: list[dict[str, Any]] = []
    for field, rows in unresolved_by_field.items():
        resolved = resolved_by_field.get(field, 0)
        rate = resolved / (resolved + len(rows))
        if 0 < rate < REFERENCE_PATH_MIN_RATE:
            # 這條路徑底下**大部分**值解不開，少數解得開。少數那幾個是撞號，不是
            # 引用：`occurrences.anchors.evidence_id` 的 `E037` 是來源內部編號，
            # 而庫裡剛好有幾個 evidence_step 的 id 長一樣。照「有人解得開就算引用
            # 欄位」判，另外 184 個本地編號會被報成斷掉的依賴。
            #
            # 不當成失敗，但也不靜靜丟掉——列出來，讓讀的人自己看那個比率。
            ambiguous_paths.append({
                "path": field,
                "resolved": resolved,
                "unresolved": len(rows),
                "sample": rows[0]["value"][:60],
            })
            continue
        if resolved == 0:
            # 這個欄位名底下沒有一個值指得到任何物件。它不是斷掉的依賴，是一個沒
            # 有對應 collection 的 ID 命名空間——照樣要報出來，因為引用它的記錄
            # 讀起來像是有東西可查。附一個樣例值，讀的人才分得出哪些是真的 ID
            # （`OCC-…` 這種），哪些只是被欄位名連累的自由文字（經文出處、標題）。
            ids_with_no_collection.append({
                "field": field,
                "count": len(rows),
                "sample": rows[0]["value"][:60],
            })
            continue
        dangling.extend(rows)
        for entry in rows:
            objects_with_gap.add((entry["collection"], entry["object_id"]))
    ids_with_no_collection.sort(key=lambda row: -row["count"])
    ambiguous_paths.sort(key=lambda row: -row["unresolved"])

    # 覆蓋誠實的第二半：聲稱的來源與實際用到的來源。
    attestation_findings = _audit_attestation_sources(store)
    for entry in attestation_findings:
        objects_with_gap.add(("argument_route_attestations", entry["object_id"]))
    scripture_findings = _audit_derived_scripture(store)
    for entry in scripture_findings:
        objects_with_gap.add(("argument_route_attestations", entry["object_id"]))
    locator_findings = _audit_component_locators(store)
    for entry in locator_findings:
        objects_with_gap.add(("viewpoint_claim_links", entry["object_id"]))

    # 原件本身：source_documents 說的雜湊，與磁碟上這份檔案現在的雜湊。
    document_findings: list[dict[str, Any]] = []
    for source_id, payload in sources.documents.items():
        source_file = sources.file_for(source_id)
        if source_file is None:
            document_findings.append({
                "source_id": source_id,
                "verdict": "no_file_on_disk",
                "detail": sources.unresolved.get(source_id, "unknown"),
            })
            continue
        claimed = str(payload.get("source_sha256") or "")
        if claimed and claimed != source_file.sha256:
            document_findings.append({
                "source_id": source_id,
                "verdict": "sha_mismatch",
                "detail": f"記錄 {claimed[:12]}…，磁碟 {source_file.sha256[:12]}…",
            })

    return {
        "layer": 2,
        "name": "覆蓋誠實",
        "checked_objects": checked_total,
        "checked_clean": checked_total - len(objects_with_gap),
        "approved_objects": approved_total,
        "approved_clean": approved_total - len(objects_with_gap & approved_ids),
        "references_resolved": sum(resolved_by_field.values()),
        "references_to_retired": len(retired_targets),
        "retired_targets": retired_targets[:200],
        "references_dangling": len(dangling),
        "dangling": dangling[:200],
        "ids_with_no_collection": ids_with_no_collection,
        "ambiguous_paths": ambiguous_paths,
        "attestation_findings": attestation_findings,
        "derived_scripture_findings": scripture_findings,
        "component_locator_findings": locator_findings,
        "source_document_findings": document_findings,
    }


def _audit_attestation_sources(store: Store) -> list[dict[str, Any]]:
    """`argument_route_attestation` 說它見證的是哪一篇，實際綁的片段來自哪一篇。

    一條 attestation 宣稱「這條路線在 `SRC-X` 這篇裡被見證」。它列的每一條
    `source_fragment_id` 都必須真的來自 `SRC-X`——否則這條見證是拿別篇的話撐起
    來的，而報告上只會看到「已見證」。
    """

    fragment_source = {
        row["object_id"]: str(row["payload"].get("source_id") or "")
        for row in store.collection("source_fragments")
    }
    findings: list[dict[str, Any]] = []
    for row in store.collection("argument_route_attestations"):
        payload = row["payload"]
        claimed_source = str(payload.get("source_id") or "")
        foreign: dict[str, str] = {}
        missing: list[str] = []
        for binding in payload.get("step_bindings") or []:
            for fragment_id in binding.get("source_fragment_ids") or []:
                actual = fragment_source.get(fragment_id)
                if actual is None:
                    missing.append(fragment_id)
                elif actual != claimed_source:
                    foreign[fragment_id] = actual
        if foreign or missing:
            findings.append({
                "object_id": row["object_id"],
                "argument_route_id": payload.get("argument_route_id"),
                "claimed_source_id": claimed_source,
                "foreign_fragments": foreign,
                "missing_fragments": missing,
            })
    return findings


def _audit_derived_scripture(store: Store) -> list[dict[str, Any]]:
    """`scripture_refs_derived` 說這條見證引了哪幾處經文，證據步驟實際引了哪幾處。

    「derived」是一句宣稱：這些出處是從綁定的證據裡推導出來的。那就照字面查——
    每一處都應該在它綁定的那些 `evidence_step` 自己的 `scripture_refs` 裡出現過。
    憑空多出來的一處，等於替教授多引了一段他沒引的經文。

    比對只看章節，不看譯名寫法：庫裡同一處經文有「馬太福音16:19」也有
    「太16:19」，那是寫法差異，不是多引。
    """

    steps = {row["object_id"]: row["payload"] for row in store.collection("evidence_steps")}
    findings: list[dict[str, Any]] = []
    for row in store.collection("argument_route_attestations"):
        payload = row["payload"]
        declared = [str(x) for x in payload.get("scripture_refs_derived") or []]
        if not declared:
            continue
        actual: set[str] = set()
        for binding in payload.get("step_bindings") or []:
            for step_id in binding.get("evidence_step_ids") or []:
                step = steps.get(step_id) or {}
                for ref in step.get("scripture_refs") or []:
                    actual.add(_scripture_key(str(ref)))
        undeclared = [ref for ref in declared if _scripture_key(ref) not in actual]
        if undeclared:
            findings.append({
                "object_id": row["object_id"],
                "argument_route_id": payload.get("argument_route_id"),
                "unsupported_scripture_refs": undeclared,
                "evidence_scripture_refs": sorted(actual),
            })
    return findings


def _audit_component_locators(store: Store) -> list[dict[str, Any]]:
    """`equivalent_component` 說它等價的是主張裡的哪一段，那一段真的長那樣嗎。

    設計要求成分定位「必須是結構化的，不能只憑模型說這條裡包含同一個觀點」。所以
    照字面查：`statement_component` 必須是主張 `statement` 的一段連續文字，而
    `canonical_spans` 的字元位置必須真的框到它宣稱的那段字。

    對不上分兩種，差別很大：只差標點是抄寫走樣；把主張裡**不相鄰**的兩截拼起來
    （`「捆綁」既可指禁止`），則是造出了一個教授沒有作為一體說過的句子——而觀點
    的成員資格就掛在那個句子上。
    """

    claims = {row["object_id"]: row["payload"] for row in store.collection("claims")}
    findings: list[dict[str, Any]] = []
    for row in store.collection("viewpoint_claim_links"):
        payload = row["payload"]
        if payload.get("link_type") not in MEMBER_LINK_TYPES:
            continue
        locator = payload.get("component_locator") or {}
        component = str(locator.get("statement_component") or "").strip()
        claim = claims.get(str(payload.get("claim_id") or ""))
        if claim is None or not component:
            findings.append({
                "object_id": row["object_id"],
                "claim_id": payload.get("claim_id"),
                "verdict": "claim_missing" if claim is None else "no_component_locator",
            })
            continue
        statement = str(claim.get("statement") or "")
        if component in statement:
            for span in locator.get("canonical_spans") or []:
                start, end = span.get("start_char"), span.get("end_char")
                exact = span.get("exact_text")
                if exact is None or start is None or end is None:
                    continue
                if statement[start:end] != exact:
                    findings.append({
                        "object_id": row["object_id"],
                        "claim_id": payload.get("claim_id"),
                        "verdict": "span_offsets_wrong",
                        "expected": exact[:60],
                        "at_offsets": statement[start:end][:60],
                    })
            continue
        verdict = (
            "punctuation_only"
            if re.sub(r"[，。、；：,.;:\s]", "", component)
            in re.sub(r"[，。、；：,.;:\s]", "", statement)
            else "stitched"
        )
        findings.append({
            "object_id": row["object_id"],
            "claim_id": payload.get("claim_id"),
            "verdict": verdict,
            "component": component[:80],
            "statement": statement[:120],
        })
    return findings


def _scripture_key(reference: str) -> str:
    """把一處經文出處收成可比對的鍵：書卷末字 + 章節數字。

    「馬太福音16:19」與「太16:19」收成同一個鍵；「馬太福音18:18」不會。
    """

    digits = re.sub(r"[^0-9:：\-–]", "", reference)
    book = re.sub(r"[0-9:：\-–\s（）()]", "", reference)
    return f"{book[-1:] if book else ''}{digits}"


# ---------------------------------------------------------------------------
# 獨立模型：Gemini，直接打 HTTP，不用任何 SDK
# ---------------------------------------------------------------------------


def _trust_store() -> ssl.SSLContext:
    """一個真的能驗證憑證的 SSL context。

    這台機器上的 `python3` 是 python.org 的框架版，它的 `cert.pem` 從來沒裝過，
    所以 `ssl.create_default_context()` 開箱即 `CERTIFICATE_VERIFY_FAILED`。依序
    試 certifi、系統的 CA bundle，最後才退回預設——**不關驗證**，寧可讓第 3、4
    層報呼叫失敗，也不要用一條沒驗過的連線去取審計結論。
    """

    try:
        import certifi  # noqa: PLC0415 — 有就用，沒有就往下走

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    for bundle in ("/etc/ssl/cert.pem", "/opt/homebrew/etc/openssl@3/cert.pem"):
        if Path(bundle).is_file():
            try:
                return ssl.create_default_context(cafile=bundle)
            except Exception:
                continue
    return ssl.create_default_context()


class IndependentModel:
    """第 3、4 層要判斷的部分，交給沒參與過原判定的模型。

    提議模型是 `gpt-5.6-sol`，複核模型是 `claude-opus-5`。同一個模型會犯同一種
    錯，用它們任何一個來審計自己的產物等於沒審，所以這裡用第三家。
    """

    def __init__(self, model: str, api_key: str, timeout: int = 300) -> None:
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.calls = 0
        self.ssl_context = _trust_store()

    def judge(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }).encode("utf-8")
        url = GEMINI_ENDPOINT.format(model=self.model) + f"?key={self.api_key}"
        last_error = ""
        for attempt in range(3):
            request = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout, context=self.ssl_context
                ) as response:
                    parsed = json.loads(response.read().decode("utf-8"))
                self.calls += 1
                text = parsed["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(text)
            # `OSError` 而不是 `URLError`：讀取逾時丟的是 `TimeoutError`，它是
            # `OSError` 的子類但不是 `URLError` 的，抓窄了會讓整支審計在第 17 條
            # 抽樣上崩掉。
            except (OSError, KeyError, IndexError, ValueError) as error:
                last_error = str(error)
                time.sleep(2 * (attempt + 1))
        return {"verdict": "model_error", "reason": last_error[:400]}


CLAIM_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["supported", "disputed"]},
        "issue": {
            "type": "string",
            "enum": ["none", "overreach", "unsupported", "misattributed", "other"],
        },
        "reason": {"type": "string"},
        "quote": {"type": "string"},
    },
    "required": ["verdict", "issue", "reason"],
}

VIEWPOINT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["same", "disputed"]},
        "issue": {
            "type": "string",
            "enum": ["none", "scope_mismatch", "different_proposition", "over_merge", "other"],
        },
        "reason": {"type": "string"},
        "claim_ids_in_question": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "issue", "reason"],
}


# ---------------------------------------------------------------------------
# 第 3 層：主張站得住
# ---------------------------------------------------------------------------


def _paragraphs_for_fragments(
    fragment_ids: Iterable[str],
    fragments: dict[str, dict[str, Any]],
    sources: SourceIndex,
    limit: int = 6,
) -> list[str]:
    """片段所指的那幾段**原文**，從磁碟上現讀。

    刻意不用片段存下來的 `verbatim_excerpt`：那是流水線挑出來的一句，正好是要被
    審的東西。模型要看的是教授在那一段裡整段說了什麼。
    """

    seen: list[str] = []
    for fragment_id in fragment_ids:
        payload = fragments.get(fragment_id)
        if payload is None:
            continue
        source_file = sources.file_for(str(payload.get("source_id") or ""))
        if source_file is None:
            continue
        segment = None
        key = str(payload.get("paragraph_key") or "")
        match = re.match(r"^S(\d+)$", key)
        if match:
            segment = source_file.by_ordinal(int(match.group(1)))
        if segment is None and payload.get("source_segment_index") is not None:
            segment = source_file.by_index(payload["source_segment_index"])
        if segment is None:
            continue
        text = live_text(segment.get("text", "")).strip()
        if text and text not in seen:
            seen.append(text)
        if len(seen) >= limit:
            break
    return seen


def audit_claims(
    store: Store,
    sources: SourceIndex,
    model: IndependentModel,
    sample_size: int,
    rng: random.Random,
) -> dict[str, Any]:
    """抽樣問一件確定性檢查問不出來的事：這條主張能不能從它引的證據推出來。"""

    # 抽的是**文庫現在有的**主張，不是其中已批准的那 6 條。主張層絕大多數還掛在
    # `candidate`，只抽已批准的等於用一個 6 條的母體去代表 1,587 條——那個比率沒
    # 有意義。抽到的 `review_status` 分布照樣報出來，讀的人自己判斷代表性。
    claims = store.collection("claims")
    steps = {row["object_id"]: row["payload"] for row in store.collection("evidence_steps")}
    fragments = {row["object_id"]: row["payload"] for row in store.collection("source_fragments")}

    eligible = [row for row in claims if _claim_evidence_ids(row["payload"])]
    sample = rng.sample(eligible, min(sample_size, len(eligible)))
    status_mix = Counter(row["review_status"] for row in sample)

    results: list[dict[str, Any]] = []
    for row in sample:
        payload = row["payload"]
        evidence_ids = _claim_evidence_ids(payload)
        evidence_lines = []
        fragment_ids = []
        for step_id in evidence_ids[:10]:
            step = steps.get(step_id)
            if step is None:
                evidence_lines.append(f"- [{step_id}] （這條證據步驟在庫裡找不到）")
                continue
            refs = "、".join(step.get("scripture_refs") or []) or "無"
            evidence_lines.append(
                f"- [{step_id}]（{step.get('function') or '未標'}／經文：{refs}）"
                f"{str(step.get('statement') or '').strip()}"
            )
            if step.get("source_fragment_id"):
                fragment_ids.append(str(step["source_fragment_id"]))
        paragraphs = _paragraphs_for_fragments(fragment_ids, fragments, sources)

        prompt = _claim_prompt(row["object_id"], payload, evidence_lines, paragraphs)
        verdict = model.judge(prompt, CLAIM_SCHEMA)
        results.append({
            "claim_id": row["object_id"],
            "review_status": row["review_status"],
            "statement": str(payload.get("statement") or "")[:200],
            "evidence_step_ids": evidence_ids[:10],
            **verdict,
        })

    disputed = [r for r in results if r.get("verdict") == "disputed"]
    errors = [r for r in results if r.get("verdict") == "model_error"]
    return {
        "layer": 3,
        "name": "主張站得住",
        "population": len(eligible),
        "sampled": len(results),
        "judged": len(results) - len(errors),
        "disputed": len(disputed),
        "model_errors": len(errors),
        "review_status_mix": dict(status_mix),
        "results": results,
    }


def _claim_evidence_ids(payload: dict[str, Any]) -> list[str]:
    """一條主張真正拿來當支持的證據步驟。

    `eligible_evidence_step_ids` 是流水線區分過的「可作支持」那一批；沒有這個欄
    位的舊記錄退回 `evidence_step_ids`。`context_evidence_step_ids` 不算——那是
    聽眾發言和被駁斥的讀法。
    """

    for key in ("eligible_evidence_step_ids", "evidence_step_ids"):
        values = payload.get(key)
        if isinstance(values, list) and values:
            return [str(v) for v in values]
    return []


def _claim_prompt(
    claim_id: str, payload: dict[str, Any], evidence_lines: list[str], paragraphs: list[str]
) -> str:
    source_block = "\n\n".join(f"【原文】{p}" for p in paragraphs) or "（沒有可讀的原文段落）"
    return f"""你在審計一個講道知識庫。庫裡的每一條「主張」都聲稱是從下面列出的「證據步驟」推出來的，而證據步驟又錨定在講員的逐字稿原文上。

你的任務只有一件：**判斷這條主張能不能從這些證據推出來。**

不要判斷這條主張在神學上對不對，也不要判斷它寫得好不好。只判斷「證據撐不撐得起它」。

判 `disputed` 的情形：
- `overreach`——結論比證據走得更遠（多了一個限定詞、把「可能」寫成「是」、把一個例子推成通則）；
- `unsupported`——證據裡根本沒有講這件事；
- `misattributed`——那段話是聽眾說的，或是講員引來反駁的別人的說法，不是他自己的立場；
- `other`——其他你說得出理由的問題。

證據支持得住就判 `supported`，`issue` 填 `none`。證據只支持一部分而主張沒有超出那一部分，也算 `supported`。

---

主張 [{claim_id}]（類型：{payload.get('claim_type') or '未標'}）：
{str(payload.get('statement') or '').strip()}

證據步驟：
{chr(10).join(evidence_lines) or '（沒有列出證據步驟）'}

證據所錨定的逐字稿原文：
{source_block}

---

用 JSON 回答。`reason` 用中文，一到三句，說清楚是哪一句撐不住。`quote` 填你據以判斷的原文或證據原句（沒有就留空）。"""


# ---------------------------------------------------------------------------
# 第 4 層：觀點歸併對
# ---------------------------------------------------------------------------


def audit_viewpoints(
    store: Store,
    sources: SourceIndex,
    model: IndependentModel,
    sample_size: int,
    rng: random.Random,
) -> dict[str, Any]:
    """抽樣問：判為同一個觀點的那幾條主張，真值條件是不是真的一致。

    這是 `ViewpointQualityReport` 問不出來的那一半。它的 `review_status` 固定是
    `system_verified`、`eligibility_decision` 只有 `pass/fail/partial_internal_only`
    ——查的是引用解不解析得開，不是歸併判得對不對。
    """

    viewpoints = {row["object_id"]: row["payload"] for row in store.collection("canonical_viewpoints")}
    revisions = {row["object_id"]: row["payload"] for row in store.collection("viewpoint_revisions")}
    claims = {row["object_id"]: row["payload"] for row in store.collection("claims")}
    fragments = {row["object_id"]: row["payload"] for row in store.collection("source_fragments")}

    links_by_viewpoint: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in store.collection("viewpoint_claim_links"):
        payload = row["payload"]
        if payload.get("effective_state") not in (None, "active"):
            continue
        if payload.get("link_type") not in MEMBER_LINK_TYPES:
            continue
        links_by_viewpoint[str(payload.get("viewpoint_id") or "")].append(payload)

    # 只有兩條以上**成員**主張的觀點才談得上「歸併對不對」；一條主張的觀點沒有
    # 歸併可審。
    candidates = [
        viewpoint_id
        for viewpoint_id, links in links_by_viewpoint.items()
        if len(links) >= 2 and viewpoint_id in viewpoints
    ]
    sample = rng.sample(candidates, min(sample_size, len(candidates)))

    results: list[dict[str, Any]] = []
    for viewpoint_id in sample:
        viewpoint = viewpoints[viewpoint_id]
        revision = revisions.get(str(viewpoint.get("current_revision_id") or ""))
        if revision is None:
            results.append({
                "viewpoint_id": viewpoint_id,
                "verdict": "disputed",
                "issue": "other",
                "reason": "current_revision_id 指不到任何 viewpoint_revision，無從判斷這個觀點主張什麼",
            })
            continue
        blocks = []
        for link in links_by_viewpoint[viewpoint_id]:
            claim_id = str(link.get("claim_id") or "")
            claim = claims.get(claim_id) or {}
            locator = link.get("component_locator") or {}
            component = str(locator.get("statement_component") or "").strip()
            fragment_ids = [
                str(b.get("source_fragment_id"))
                for b in link.get("evidence_bindings") or []
                if b.get("source_fragment_id")
            ]
            paragraphs = _paragraphs_for_fragments(fragment_ids, fragments, sources, limit=2)
            source_block = "\n".join(f"    【原文】{p}" for p in paragraphs) or "    （沒有可讀的原文段落）"
            blocks.append(
                f"- 主張 [{claim_id}]（連結類型 {link.get('link_type')}）\n"
                f"    被歸入的成分：{component or '（未記錄）'}\n"
                f"    主張全文：{str(claim.get('statement') or '（庫裡找不到這條主張）').strip()}\n"
                f"{source_block}"
            )

        prompt = _viewpoint_prompt(viewpoint_id, revision, blocks)
        verdict = model.judge(prompt, VIEWPOINT_SCHEMA)
        results.append({
            "viewpoint_id": viewpoint_id,
            "revision_id": viewpoint.get("current_revision_id"),
            "core_proposition": str(revision.get("core_proposition") or "")[:200],
            "linked_claims": len(links_by_viewpoint[viewpoint_id]),
            **verdict,
        })

    disputed = [r for r in results if r.get("verdict") == "disputed"]
    errors = [r for r in results if r.get("verdict") == "model_error"]
    return {
        "layer": 4,
        "name": "觀點歸併對",
        "population": len(candidates),
        "sampled": len(results),
        "judged": len(results) - len(errors),
        "disputed": len(disputed),
        "model_errors": len(errors),
        "results": results,
    }


def _viewpoint_prompt(viewpoint_id: str, revision: dict[str, Any], blocks: list[str]) -> str:
    signature = revision.get("proposition_signature") or {}
    signature_line = " / ".join(
        str(signature.get(key) or "—")
        for key in ("subject", "predicate", "object", "polarity", "modality")
    )
    conditions = "、".join(signature.get("conditions") or []) or "無"
    return f"""你在審計一個講道知識庫的觀點層。庫裡把來自不同講道的多條「主張」判定為同一個「觀點」的成員。

判成成員的意思是**等價**：不是「相關」、不是「支持」、不是「加了限定」，而是真值條件相同。每條主張下面標出的「被歸入的成分」，就是庫裡宣稱與這個觀點等價的那一段；主張的其餘部分不在這次判斷之內。

你的任務只有一件：**判斷這些成分是不是真的與觀點的核心命題具有相同的真值條件**——也就是，它們會不會在同樣的情況下為真、同樣的情況下為假。

不要判斷這個觀點在神學上對不對。只判斷「把這幾條放在一起，算不算同一個觀點」。

判 `disputed` 的情形：
- `scope_mismatch`——某條主張的適用範圍（對象、人群、時態、條件）比觀點窄或寬，兩者不會同真同假；
- `different_proposition`——某條主張講的根本是另一件事，只是用詞相近；
- `over_merge`——觀點的表述把兩個可以分開的命題用「和」「以及」併在一起，而有些主張只支持其中一半；
- `other`——其他你說得出理由的問題。

都對得上就判 `same`，`issue` 填 `none`。

---

觀點 [{viewpoint_id}]
核心命題：{str(revision.get('core_proposition') or '').strip()}
命題簽名（主語 / 謂語 / 賓語 / 極性 / 模態）：{signature_line}
限定條件：{conditions}
經文範圍：{'、'.join(revision.get('scope', {}).get('scripture_scope') or []) or '未限定'}

被判定為同一觀點的主張：
{chr(10).join(blocks)}

---

用 JSON 回答。`reason` 用中文，一到三句。`claim_ids_in_question` 填有問題的那幾條主張的 ID（沒有就留空陣列）。"""


# ---------------------------------------------------------------------------
# 報告
# ---------------------------------------------------------------------------


def build_human_queue(layers: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """有異議的進人工佇列，與 Solution Architecture D4 第 4 步同一個出口。

    審計只讀：這裡產出的是待人判斷的條目，不是待套用的修改。
    """

    items: list[dict[str, Any]] = []
    for finding in layers.get(1, {}).get("findings", []):
        if finding["verdict"] in (
            "misplaced", "deleted_text_only", "absent", "no_source_file", "punctuation_only"
        ):
            items.append({
                "kind": "fragment_anchor",
                "object_id": finding["fragment_id"],
                "verdict": finding["verdict"],
                "detail": finding.get("detail", ""),
            })
    for entry in layers.get(2, {}).get("dangling", []):
        items.append({
            "kind": "dangling_reference",
            "object_id": entry["object_id"],
            "verdict": "unresolvable_dependency",
            "detail": f"{entry['collection']}.{entry['field']} → {entry['value']}",
        })
    for entry in layers.get(2, {}).get("attestation_findings", []):
        items.append({
            "kind": "attestation_source",
            "object_id": entry["object_id"],
            "verdict": "foreign_source_fragment",
            "detail": json.dumps(entry, ensure_ascii=False),
        })
    for entry in layers.get(2, {}).get("component_locator_findings", []):
        items.append({
            "kind": "component_locator",
            "object_id": entry["object_id"],
            "verdict": entry["verdict"],
            "detail": f"claim {entry.get('claim_id')}：{entry.get('component', '')}",
        })
    for entry in layers.get(2, {}).get("derived_scripture_findings", []):
        items.append({
            "kind": "derived_scripture",
            "object_id": entry["object_id"],
            "verdict": "unsupported_scripture_ref",
            "detail": "、".join(entry["unsupported_scripture_refs"]),
        })
    for entry in layers.get(3, {}).get("results", []):
        if entry.get("verdict") == "disputed":
            items.append({
                "kind": "claim_support",
                "object_id": entry["claim_id"],
                "verdict": entry.get("issue") or "disputed",
                "detail": entry.get("reason", ""),
            })
    for entry in layers.get(4, {}).get("results", []):
        if entry.get("verdict") == "disputed":
            items.append({
                "kind": "viewpoint_identity",
                "object_id": entry["viewpoint_id"],
                "verdict": entry.get("issue") or "disputed",
                "detail": entry.get("reason", ""),
            })
    return {
        "schema_version": "wang_library_audit_human_queue_v1",
        "disposition": "pending",
        "items": items,
    }


def _anchor_state_disagrees(key: str) -> bool:
    """審計判定與片段自己宣告的 `anchor_state` 是否互相矛盾。

    宣告 `source_version_bound` 卻對不上原件，或宣告 `unresolved` 卻好端端地對得
    上——兩種都是流水線自檢說了不算的地方，也正是這支程式存在的理由。
    """

    verdict, _, state = key.partition(" / ")
    bound = state.strip() in ("source_version_bound", "canonical_citation_bound")
    return bound != (verdict == "pass")


def ratio(passed: int, total: int) -> str:
    if not total:
        return "—"
    return f"{passed:,}/{total:,}  ({passed / total:.1%})"


def render_report(
    layers: dict[int, dict[str, Any]], meta: dict[str, Any], queue_size: int
) -> str:
    lines = [
        "王教授文庫獨立審計",
        f"時間        {meta['generated_at']}",
        f"審計模型    {meta['model']}（第 3、4 層；提議與複核模型未參與）",
        f"語料        {meta['sources']} 份來源 · {meta['fragments']:,} 條片段 · "
        f"{meta['claims']:,} 條主張 · {meta['viewpoints']} 個觀點",
        "",
    ]

    if 1 in layers:
        layer = layers[1]
        lines.append(f"第 1 層 逐字對得上   {ratio(layer['passed'], layer['total'])}")
        version = layer["version_binding"]
        lines.append(
            f"          錨點記的來源版本：與磁碟一致 {version.get('current', 0):,} · "
            f"已過期 {version.get('stale', 0):,} · 未記錄 {version.get('unclaimed', 0):,}"
        )
        for verdict, count in sorted(layer["counts"].items(), key=lambda kv: -kv[1]):
            if verdict in ("pass", "one_locator_only"):
                continue
            lines.append(f"          {verdict:<22} {count:>6,}")
        if layer["counts"].get("one_locator_only"):
            lines.append(
                f"          {'one_locator_only':<22} {layer['counts']['one_locator_only']:>6,}"
                "   （引文對得上，但兩個定位器只有一個指得對）"
            )
        disagreements = [
            f"{key}={count}"
            for key, count in layer["verdict_by_declared_anchor_state"].items()
            if _anchor_state_disagrees(key)
        ]
        lines.append(
            "          與 anchor_state 的分歧：" + ("、".join(disagreements) if disagreements else "無")
        )
        lines.append("")

    if 2 in layers:
        layer = layers[2]
        lines.append(
            f"第 2 層 覆蓋誠實     {ratio(layer['checked_clean'], layer['checked_objects'])}"
            "   （當前物件中依賴全部可解析的）"
        )
        lines.append(
            f"          其中 review_status 已批准的 "
            f"{ratio(layer['approved_clean'], layer['approved_objects'])}"
        )
        lines.append(
            f"          已解析引用 {layer['references_resolved']:,} · "
            f"其中指向已 retire 的 {layer['references_to_retired']:,} · "
            f"斷掉 {layer['references_dangling']:,}"
        )
        lines.append(
            f"          attestation 用了別篇的片段 {len(layer['attestation_findings']):>4,}"
        )
        lines.append(
            f"          scripture_refs_derived 無證據支持 {len(layer['derived_scripture_findings']):>4,}"
        )
        locator_mix = Counter(entry["verdict"] for entry in layer["component_locator_findings"])
        lines.append(
            f"          成分定位對不上主張原文 {sum(locator_mix.values()):>4,}"
            + (f"   （{'、'.join(f'{k} {v}' for k, v in sorted(locator_mix.items()))}）" if locator_mix else "")
        )
        lines.append(
            f"          原件雜湊與記錄不符 {len(layer['source_document_findings']):>4,}"
        )
        for entry in layer["ambiguous_paths"][:4]:
            lines.append(
                f"          撞號的路徑 {entry['path']:<34} 解得開 {entry['resolved']:,}"
                f" · 解不開 {entry['unresolved']:,}   例：{entry['sample'][:20]}"
            )
        for entry in layer["ids_with_no_collection"][:6]:
            lines.append(
                f"          查無此物的 ID {entry['field']:<26} {entry['count']:>5,}"
                f"   例：{entry['sample'][:24]}"
            )
        lines.append("")

    if 3 in layers:
        layer = layers[3]
        lines.append(
            f"第 3 層 主張抽樣     {layer['judged']} 條中 {layer['disputed']} 條有異議"
            f"   （母體 {layer['population']:,} 條）"
        )
        mix = "、".join(f"{k} {v}" for k, v in sorted(layer["review_status_mix"].items()))
        lines.append(f"          抽樣的 review_status：{mix}")
        for entry in layer["results"]:
            if entry.get("verdict") == "disputed":
                lines.append(f"          [{entry['issue']}] {entry['claim_id']}")
                lines.append(f"              {entry['reason'][:88]}")
        if layer["model_errors"]:
            lines.append(f"          模型呼叫失敗 {layer['model_errors']}")
        lines.append("")

    if 4 in layers:
        layer = layers[4]
        lines.append(
            f"第 4 層 觀點抽樣     {layer['judged']} 個中 {layer['disputed']} 個有異議"
            f"   （母體 {layer['population']} 個）"
        )
        for entry in layer["results"]:
            if entry.get("verdict") == "disputed":
                lines.append(f"          [{entry['issue']}] {entry['viewpoint_id']}")
                lines.append(f"              {entry['reason'][:88]}")
        if layer["model_errors"]:
            lines.append(f"          模型呼叫失敗 {layer['model_errors']}")
        lines.append("")

    lines.append(f"人工佇列    {queue_size:,} 條待判斷")
    return "\n".join(lines)


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="文庫獨立完整性與正確性審計（只讀，不寫入 PostgreSQL）"
    )
    parser.add_argument(
        "--layers", default="1,2,3,4", help="要跑哪幾層，逗號分隔。預設四層全跑"
    )
    parser.add_argument("--claims", type=int, default=20, help="第 3 層抽幾條主張")
    parser.add_argument("--viewpoints", type=int, default=10, help="第 4 層抽幾個觀點")
    parser.add_argument("--seed", type=int, default=241, help="抽樣種子，同種子可重放")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="第 3、4 層用的獨立模型")
    parser.add_argument("--out", type=Path, default=None, help="報告輸出目錄")
    args = parser.parse_args(argv)

    wanted = {int(x) for x in args.layers.split(",") if x.strip()}
    settings = load_settings()
    database_url = settings.get("KNOWLEDGE_DATABASE_URL")
    if not database_url:
        raise SystemExit("KNOWLEDGE_DATABASE_URL 未設定")
    data_base_dir = Path(settings.get("DATA_BASE_DIR", ""))
    if not data_base_dir.is_dir():
        raise SystemExit(f"DATA_BASE_DIR 不是目錄：{data_base_dir}")

    store = Store(database_url)
    sources = SourceIndex(store.collection("source_documents"), data_base_dir)
    rng = random.Random(args.seed)

    model: IndependentModel | None = None
    if wanted & {3, 4}:
        api_key = settings.get("GEMINI_API_KEY1", "")
        if not api_key:
            raise SystemExit("第 3、4 層需要 GEMINI_API_KEY1；或用 --layers 1,2 只跑確定性的兩層")
        model = IndependentModel(args.model, api_key)

    layers: dict[int, dict[str, Any]] = {}
    if 1 in wanted:
        print("第 1 層 逐字對得上 …", file=sys.stderr)
        layers[1] = audit_verbatim(store, sources)
    if 2 in wanted:
        print("第 2 層 覆蓋誠實 …", file=sys.stderr)
        layers[2] = audit_coverage(store, sources)
    if 3 in wanted and model is not None:
        print(f"第 3 層 主張抽樣（{args.claims} 條）…", file=sys.stderr)
        layers[3] = audit_claims(store, sources, model, args.claims, rng)
    if 4 in wanted and model is not None:
        print(f"第 4 層 觀點抽樣（{args.viewpoints} 個）…", file=sys.stderr)
        layers[4] = audit_viewpoints(store, sources, model, args.viewpoints, rng)

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": args.model if model else "未使用",
        "seed": args.seed,
        "sources": len(sources.documents),
        "fragments": len(store.collection("source_fragments")),
        "claims": len(store.collection("claims")),
        "viewpoints": len(store.collection("canonical_viewpoints")),
    }
    queue = build_human_queue(layers)
    report = render_report(layers, meta, len(queue["items"]))

    out_dir = args.out or (
        data_base_dir
        / "wang-knowledge-platform/staging/reports/library-audit"
        / datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.txt").write_text(report + "\n", encoding="utf-8")
    (out_dir / "audit.json").write_text(
        json.dumps(
            {"schema_version": "wang_library_audit_v1", "meta": meta, "layers": layers},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "human-queue.json").write_text(
        json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(report)
    print(f"\n輸出：{out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
