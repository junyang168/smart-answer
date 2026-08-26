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
    scripts/audit-library.py --claims 40 --viewpoints 15   # 抽樣，改 prompt 時用
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
import threading
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
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

    def collection(self, name: str, *, include_retired: bool = False) -> list[dict[str, Any]]:
        """一個 collection 的物件。預設不含已 retire 的。

        `include_retired` 是給**取材**用的，不是給判定用的。一條被 retire 的
        `evidence_step` 或 `source_fragment` 仍然是教授說過的話，只是不再是當前
        版本；讀不到它，第 3 層就只能拿一個空包去問模型，而模型會誠實地回答
        「沒有證據支持」——那句話說的是我的包，不是文庫。
        """

        key = f"{name}:{include_retired}"
        if key not in self._cache:
            where = "" if include_retired else "and retired_at is null "
            self._cache[key] = self._query(
                "select json_build_object("
                "'object_id', object_id, 'review_status', review_status,"
                "'revision', revision, 'retired', (retired_at is not null),"
                "'payload', payload)::text "
                f"from wang_knowledge.objects where collection = '{name}' "
                f"{where}order by object_id"
            )
        return self._cache[key]

    def collection_names(self) -> list[str]:
        rows = self._query(
            "select json_build_object('collection', collection)::text "
            "from wang_knowledge.objects group by collection order by collection"
        )
        return [row["collection"] for row in rows]

    def ingested_subjects(self) -> set[str]:
        """What the run ledger says actually landed in the store.

        `pipeline_runs` is the pipeline's own record, so this is the one place
        the audit takes the pipeline's word for something. That is deliberate
        and narrow: the ledger decides **which rows to look at**, never whether
        those rows are right. Every judgement below still comes from the files
        on disk.

        The alternative is worse. The store holds 35 sources while the current
        run covers 20 -- the other 13 are Romans-era material from earlier
        batches -- so a ratio over everything answers a question nobody asked,
        and averages the obsolete rows in with the ones about to be built on.
        """

        rows = self._query(
            "select json_build_object('s', subject_id)::text "
            "from wang_knowledge.pipeline_runs "
            "where stage = 'ingest' and status = 'succeeded' group by subject_id"
        )
        return {row["s"] for row in rows}

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

    def ordinal_of(self, segment: dict[str, Any]) -> int | None:
        """這一段是第幾段（1-based），用來取它前面那一段。"""

        for position, candidate in enumerate(self.segments, start=1):
            if candidate is segment:
                return position
        return None

    def by_index(self, index: Any) -> dict[str, Any] | None:
        """`index` 欄位等於這個值的那一段，也就是 `source_segment_index`。"""

        return self._by_index.get(str(index))


class SourceIndex:
    """`source_id` → 磁碟上的原件。"""

    def __init__(
        self,
        documents: list[dict[str, Any]],
        data_base_dir: Path,
        in_scope: set[str] | None = None,
    ) -> None:
        self.data_base_dir = data_base_dir
        self.documents = {row["object_id"]: row["payload"] for row in documents}
        #: `None` 表示不限範圍。否則只有這些 `source_id` 算數。
        self.in_scope = in_scope
        self.out_of_scope = (
            sorted(set(self.documents) - in_scope) if in_scope is not None else []
        )
        self._files: dict[str, SourceFile | None] = {}
        self.unresolved: dict[str, str] = {}

    def covers(self, source_id: str) -> bool:
        return self.in_scope is None or source_id in self.in_scope

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

    skipped_out_of_scope = 0
    for row in fragments:
        payload = row["payload"]
        fragment_id = row["object_id"]
        source_id = str(payload.get("source_id") or "")
        if not sources.covers(source_id):
            skipped_out_of_scope += 1
            continue
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
        "out_of_scope": skipped_out_of_scope,
        "total": len(fragments) - skipped_out_of_scope,
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
    struck_evidence = _audit_struck_evidence(store, sources)
    for entry in struck_evidence:
        objects_with_gap.add(("claims", entry["object_id"]))
    retired_evidence = _audit_retired_evidence(store)
    for entry in retired_evidence:
        objects_with_gap.add(("claims", entry["object_id"]))
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
        "retired_evidence_findings": retired_evidence,
        "struck_evidence_findings": struck_evidence,
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


def _audit_struck_evidence(store: Store, sources: SourceIndex) -> list[dict[str, Any]]:
    """主張的證據，引文只活在校對者劃掉的文字裡。

    三份逐字稿被劃掉的比例是 23%、41%、43%——校對者刪掉的不是幾個錯字，是整段
    整段的內容。一條主張如果它的證據落在那些字上，那條主張現在講的是逐字稿裡
    已經不存在的話。

    這一類非報不可，而且必須報成它自己：讓模型去判，模型只會說「證據裡沒有這
    件事」，聽起來像教授沒講過——他講過，是後來被劃掉了。兩件事的處置完全不同。
    """

    frags = {
        row["object_id"]: row["payload"]
        for row in store.collection("source_fragments", include_retired=True)
    }
    steps = {
        row["object_id"]: row["payload"]
        for row in store.collection("evidence_steps", include_retired=True)
    }
    findings: list[dict[str, Any]] = []
    for row in store.collection("claims"):
        ids = _claim_evidence_ids(row["payload"])
        if not ids:
            continue
        struck: list[str] = []
        alive = 0
        for step_id in ids:
            step = steps.get(step_id)
            for fragment_id in _step_fragment_ids(step) if step else []:
                payload = frags.get(fragment_id)
                if payload is None:
                    continue
                source_file = sources.file_for(str(payload.get("source_id") or ""))
                excerpt = str(payload.get("verbatim_excerpt") or "")
                if source_file is None or not excerpt:
                    continue
                if excerpt in source_file.live_whole:
                    alive += 1
                elif excerpt in source_file.raw_whole:
                    struck.append(fragment_id)
        if struck:
            findings.append({
                "object_id": row["object_id"],
                "verdict": "all_evidence_struck" if not alive else "some_evidence_struck",
                "struck_fragment_ids": struck,
                "statement": str(row["payload"].get("statement") or "")[:120],
            })
    return findings


def _audit_retired_evidence(store: Store) -> list[dict[str, Any]]:
    """主張引的證據步驟已經被 retire。

    這是確定性的事實，不必問模型：一條主張如果它引的證據步驟全部退役了，那條
    主張現在沒有當前的證據撐著。第 2 層查引用解不解析得開，而指向 retire 物件
    的引用算「解得開」（記錄還在），所以這一類從那裡漏了下去——然後在第 3 層以
    「證據裡沒有這件事」的樣子冒出來，看起來像是教授的內容有問題。

    分兩級：全部退役是斷了，部分退役是缺了一塊。兩種都要人看，但不是同一件事。
    """

    retired_steps = {
        row["object_id"]
        for row in store.collection("evidence_steps", include_retired=True)
        if row.get("retired")
    }
    if not retired_steps:
        return []
    findings: list[dict[str, Any]] = []
    for row in store.collection("claims"):
        ids = _claim_evidence_ids(row["payload"])
        if not ids:
            continue
        stale = [i for i in ids if i in retired_steps]
        if not stale:
            continue
        findings.append({
            "object_id": row["object_id"],
            "verdict": "all_evidence_retired" if len(stale) == len(ids) else "some_evidence_retired",
            "retired_step_ids": stale,
            "evidence_step_ids": ids,
            "statement": str(row["payload"].get("statement") or "")[:120],
        })
    return findings


def _audit_component_locators(store: Store) -> list[dict[str, Any]]:
    """`equivalent_component` 說它等價的是主張裡的哪一段，那一段真的長那樣嗎。

    設計要求成分定位「必須是結構化的，不能只憑模型說這條裡包含同一個觀點」。
    結構化的意思在 `canonical_spans` 上：它是一個**列表**，因為中文的並提句要
    拆開才對得上。`「捆綁」和「釋放」既可指禁止與准許某事` 拆成 捆綁→禁止、
    釋放→准許某事，兩個觀點各拿一半，各自兩段不相鄰的字元區間。

    所以查的是三件事，而**不是**「`statement_component` 是不是一段連續文字」：
    第一版就是那樣查的，於是把兩條正確的並提拆解報成了造句。

    1. 每一段 span 的 `exact_text`，真的在它記的字元位置上；
    2. `statement_component` 等於那幾段依序接起來——它是摘要，不是第三個事實；
    3. 沒有 `canonical_spans` 的舊記錄，退回「必須是一段連續文字」，因為那時
       沒有別的東西可以驗。
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
        spans = locator.get("canonical_spans") or []

        if not spans:
            if component in statement:
                continue
            verdict = (
                "punctuation_variant"
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
            continue

        misplaced = []
        pieces = []
        for span in spans:
            start_char, end_char = span.get("start_char"), span.get("end_char")
            exact = span.get("exact_text")
            if exact is None or start_char is None or end_char is None:
                continue
            pieces.append(str(exact))
            if statement[start_char:end_char] != exact:
                misplaced.append({
                    "expected": str(exact)[:60],
                    "at_offsets": statement[start_char:end_char][:60],
                })
        if misplaced:
            findings.append({
                "object_id": row["object_id"],
                "claim_id": payload.get("claim_id"),
                "verdict": "span_offsets_wrong",
                "spans": misplaced,
                "statement": statement[:120],
            })
            continue
        if "".join(pieces) != component:
            findings.append({
                "object_id": row["object_id"],
                "claim_id": payload.get("claim_id"),
                "verdict": "component_not_from_spans",
                "component": component[:80],
                "spans_joined": "".join(pieces)[:80],
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

    def __init__(
        self,
        model: str,
        api_key: str,
        timeout: int = 300,
        cache_dir: Path | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.calls = 0
        self.cached = 0
        self.ssl_context = _trust_store()
        #: 判讀結果按 prompt 的 sha256 存檔。溫度是 0，同一個 prompt 的答案不會
        #: 變，所以重跑只需要為**變過的**那幾條付錢——1,408 條全查一次要幾分鐘，
        #: 中途斷掉重來一次就不必再等一次。
        self.cache_dir = cache_dir
        self._lock = threading.Lock()
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, prompt: str) -> Path | None:
        if self.cache_dir is None:
            return None
        key = hashlib.sha256(f"{self.model}\n{prompt}".encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.json"

    def judge(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        cached = self._cache_path(prompt)
        if cached is not None and cached.is_file():
            try:
                with self._lock:
                    self.cached += 1
                return json.loads(cached.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
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
                with self._lock:
                    self.calls += 1
                blocked = (parsed.get("promptFeedback") or {}).get("blockReason")
                if blocked:
                    # 安全過濾器擋下了 prompt。這是決定性的，重試三次只是把同一
                    # 個拒絕再要兩次。
                    #
                    # 它擋的是什麼值得記下來：擋掉的兩條是「離婚要按具體情況判
                    # 斷」和「看見女性漂亮不等於心裡犯姦淫」——都是再平常不過的
                    # 釋經。過濾器誤判的正好是牧養上最敏感的那些題目，所以這一
                    # 類必須報成「沒有人也沒有機器看過」，不能算成一次網路失敗。
                    verdict = {
                        "verdict": "blocked",
                        "issue": "other",
                        "reason": f"審計模型的安全過濾器擋下了這一條（{blocked}），沒有判讀。",
                    }
                    if cached is not None:
                        cached.write_text(
                            json.dumps(verdict, ensure_ascii=False), encoding="utf-8"
                        )
                    return verdict
                text = parsed["candidates"][0]["content"]["parts"][0]["text"]
                verdict = json.loads(text)
                if cached is not None:
                    # A model error is never cached: it is a fact about the
                    # network, not about the claim.
                    cached.write_text(
                        json.dumps(verdict, ensure_ascii=False), encoding="utf-8"
                    )
                return verdict
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


class Progress:
    """跑到哪裡了，寫成一個檔案給讀報告的人看。

    這一輪要打一千四百次模型呼叫，十來分鐘。從網頁按下去之後，如果中間什麼都
    不說，按的人只能猜它有沒有在動——而猜的結果通常是再按一次。

    刻意寫檔案而不是回報給某個服務：審計不 import 任何 `backend/` 模組，這條
    不因為多了一個進度條就放棄。誰想知道進度，自己讀這個檔案。
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.state: dict[str, Any] = {}
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def update(self, **fields: Any) -> None:
        if self.path is None:
            return
        self.state.update(fields)
        self.state["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # 先寫暫存再換名：讀的人永遠讀到一份完整的 JSON，不會讀到寫到一半的。
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)


def _judge_trimming_on_block(
    model: "IndependentModel",
    build: Any,
    paragraphs: list[str],
    schema: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """被過濾器擋下時，少送一段原文再問一次，直到它肯回答。

    Gemini 的過濾器擋的不是任何**一個**部分，是組合：主張、證據、每一段原文
    分開送都過得去，合起來就 `PROHIBITED_CONTENT`。這個理由不在 `safetySettings`
    管得到的類別裡，關不掉。

    而被擋掉的偏偏是離婚、再婚、情慾這些題目——教授講得最多、最需要被查的部
    分。留著不判，等於審計在最要緊的地方系統性地閉眼。

    所以逐段減，減到它肯判為止，並且把**減掉了幾段**記下來。少送原文的判斷比
    足額的弱，一路減到零段的那種尤其弱——那等於只拿主張比證據摘要。記下來，讀
    的人才分得出這兩種。
    """

    kept = len(paragraphs)
    while kept >= 0:
        verdict = model.judge(build(paragraphs[:kept]), schema)
        if verdict.get("verdict") != "blocked":
            return verdict, kept
        if kept == 0:
            return verdict, 0
        kept -= 1
    return {"verdict": "blocked", "issue": "other", "reason": "過濾器擋下"}, 0


def _judge_all(
    rows: list[Any],
    judge: Any,
    workers: int,
    label: str,
    progress: "Progress | None" = None,
) -> list[dict[str, Any]]:
    """把一批判讀跑完，順序與輸入一致，並在 stderr 報進度。

    一條一條打要等上四十分鐘，而每一條都是獨立的一次 HTTP 呼叫，彼此沒有先後
    關係。順序保留是為了同一個範圍重跑時輸出可比對，不是為了正確性。
    """

    results: list[dict[str, Any] | None] = [None] * len(rows)
    done = 0
    lock = threading.Lock()

    if progress is not None:
        progress.update(stage=label, done=0, total=len(rows))

    def run(index: int) -> None:
        nonlocal done
        results[index] = judge(rows[index])
        with lock:
            done += 1
            if done % 25 == 0 or done == len(rows):
                print(f"  {label} {done}/{len(rows)}", file=sys.stderr)
                if progress is not None:
                    progress.update(stage=label, done=done, total=len(rows))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(run, range(len(rows))))
    return [row for row in results if row is not None]


def _paragraphs_for_fragments(
    fragment_ids: Iterable[str],
    fragments: dict[str, dict[str, Any]],
    sources: SourceIndex,
    limit: int = 6,
) -> list[str]:
    """片段所指的那幾段原文，前後各帶一段。第 4 層還在用這個窄視窗。"""

    seen: list[str] = []
    for fragment_id in fragment_ids:
        payload = fragments.get(fragment_id)
        if payload is None:
            continue
        source_file = sources.file_for(str(payload.get("source_id") or ""))
        if source_file is None:
            continue
        segment = _segment_for(source_file, payload)
        if segment is None:
            continue
        ordinal = source_file.ordinal_of(segment)
        window = []
        if ordinal is not None and ordinal > 1:
            previous = source_file.by_ordinal(ordinal - 1)
            if previous is not None:
                window.append(live_text(previous.get("text", "")).strip())
        window.append(live_text(segment.get("text", "")).strip())
        for text in window:
            if text and text not in seen:
                seen.append(text)
        if len(seen) >= limit:
            break
    return seen


def _segment_for(source_file: SourceFile, payload: dict[str, Any]) -> dict[str, Any] | None:
    """片段記的位置，兩種寫法都認得。"""

    key = str(payload.get("paragraph_key") or "")
    match = re.match(r"^S(\d+)$", key)
    segment = None
    if match:
        segment = source_file.by_ordinal(int(match.group(1)))
    elif key:
        segment = source_file.by_index(key)
    if segment is None and payload.get("source_segment_index") is not None:
        segment = source_file.by_index(payload["source_segment_index"])
    return segment


def _whole_sources_for_claim(
    fragment_ids: Iterable[str],
    fragments: dict[str, dict[str, Any]],
    sources: SourceIndex,
) -> tuple[list[str], list[str]]:
    """整篇來源，外加錨點落在哪幾段。

    窄視窗是這一路假陽性的最後一個來源，而且是最大的一個：18 條異議裡有 12
    條，模型說「原文沒提到」的字就在同一篇逐字稿裡，只是不在送去的那兩段。
    量出來的例子——「護照」在原文裡，模型說「完全沒有提及護照與主權的類比」；
    「遷入」三次、「愛子的國」四次、「Strong」兩次、「字典」九次，全是這樣。
    
    原因不是視窗開得不夠大，是視窗這個做法本身錯了：一條主張概括的是教授在
    整篇裡鋪的一段論證，錨點只標了其中一句。講道八千到兩萬八千字，整篇送得
    起，那就整篇送——讓模型自己在裡面找，而不是我替它挑，然後把挑漏的算成文
    庫的問題。

    錨點仍然標出來，因為「證據指著哪一句」本身是要判的東西之一。
    """

    texts: list[str] = []
    anchors: list[str] = []
    seen_sources: set[str] = set()
    for fragment_id in fragment_ids:
        payload = fragments.get(fragment_id)
        if payload is None:
            continue
        source_id = str(payload.get("source_id") or "")
        source_file = sources.file_for(source_id)
        if source_file is None:
            continue
        if source_id not in seen_sources:
            seen_sources.add(source_id)
            title = str(sources.documents.get(source_id, {}).get("title") or source_id)
            body = "\n\n".join(
                live_text(segment.get("text", "")).strip()
                for segment in source_file.segments
                if live_text(segment.get("text", "")).strip()
            )
            texts.append(f"《{title}》\n{body}")
        excerpt = str(payload.get("verbatim_excerpt") or "").strip()
        if excerpt and excerpt not in anchors:
            anchors.append(excerpt)
    return texts, anchors


def audit_claims(
    store: Store,
    sources: SourceIndex,
    model: IndependentModel,
    sample_size: int | None,
    rng: random.Random,
    workers: int = 8,
    progress: "Progress | None" = None,
    only: set[str] | None = None,
) -> dict[str, Any]:
    """抽樣問一件確定性檢查問不出來的事：這條主張能不能從它引的證據推出來。"""

    # 抽的是**文庫現在有的**主張，不是其中已批准的那 6 條。主張層絕大多數還掛在
    # `candidate`，只抽已批准的等於用一個 6 條的母體去代表 1,587 條——那個比率沒
    # 有意義。抽到的 `review_status` 分布照樣報出來，讀的人自己判斷代表性。
    claims = store.collection("claims")
    # 取材連 retire 的一起讀。412 條 evidence_step 的片段全部已 retire，6 條主張
    # 的證據步驟全部已 retire——照舊只讀當前的，這些主張送進去就是空包。
    steps = {
        row["object_id"]: row["payload"]
        for row in store.collection("evidence_steps", include_retired=True)
    }
    fragments = {
        row["object_id"]: row["payload"]
        for row in store.collection("source_fragments", include_retired=True)
    }

    eligible = [
        row
        for row in claims
        if _claim_evidence_ids(row["payload"]) and _claim_in_scope(row["payload"], sources, fragments)
    ]
    # 全查，不抽樣。範圍內是 1,365 條，不是 20 萬條；而這一輪要回答的是「往下
    # 跑之前，這批到底對不對」——抽樣答不了那個問題，它只答得出「抽到的這 20
    # 條對不對」。`--claims N` 仍然可以縮小，用在改 prompt 的時候。
    if only:
        eligible = [row for row in eligible if row["object_id"] in only]
    chosen = eligible if sample_size is None else rng.sample(eligible, min(sample_size, len(eligible)))
    status_mix = Counter(row["review_status"] for row in chosen)

    def judge(row: dict[str, Any]) -> dict[str, Any]:
        payload = row["payload"]
        evidence_ids = _claim_evidence_ids(payload)
        evidence_lines = []
        fragment_ids: list[str] = []
        # 全部送，不截斷。原來寫死 `[:10]`，10 條主張的證據被砍掉一截而報告上
        # 什麼都不說——模型少看幾條證據，判出來的「證據不足」就是我造的。
        for step_id in evidence_ids:
            step = steps.get(step_id)
            if step is None:
                evidence_lines.append(f"- [{step_id}] （這條證據步驟在庫裡找不到）")
                continue
            refs = "、".join(step.get("scripture_refs") or []) or "無"
            evidence_lines.append(
                f"- [{step_id}]（{step.get('function') or '未標'}／經文：{refs}）"
                f"{str(step.get('statement') or '').strip()}"
            )
            fragment_ids.extend(_step_fragment_ids(step))
        paragraphs, anchors = _whole_sources_for_claim(fragment_ids, fragments, sources)
        if not paragraphs:
            # 拿不到一段原文就不要問模型。
            #
            # 這一層問的是「這條主張能不能從證據推出來」，而證據的根在逐字稿。
            # 一段都拿不到還去問，模型只能拿主張比證據摘要，然後誠實地回答「證
            # 據裡沒有這件事」——那句話說的是我的包，不是文庫。這一路的假陽性
            # 全是這麼來的。
            #
            # 這 18 條的片段本來就沒記位置（`DK-STEP-*` 那批 `paragraph_key` 是
            # 空的），所以它是資料的缺口，該報成缺口，不該報成主張站不住。
            return {
                "claim_id": row["object_id"],
                "review_status": row["review_status"],
                "statement": str(payload.get("statement") or "")[:200],
                "evidence_step_ids": evidence_ids,
                "source_paragraphs": 0,
                "verdict": "no_source_text",
                "issue": "none",
                "reason": "這條主張引的片段沒有記位置，取不到逐字稿，無從判讀。",
            }
        verdict, used = _judge_trimming_on_block(
            model,
            lambda kept: _claim_prompt(row["object_id"], payload, evidence_lines, kept, anchors),
            paragraphs,
            CLAIM_SCHEMA,
        )
        return {
            "claim_id": row["object_id"],
            "review_status": row["review_status"],
            "statement": str(payload.get("statement") or "")[:200],
            "evidence_step_ids": evidence_ids,
            "source_paragraphs": used,
            "paragraphs_dropped_to_pass_filter": len(paragraphs) - used,
            **verdict,
        }

    results = _judge_all(chosen, judge, workers, "主張", progress)

    disputed = [r for r in results if r.get("verdict") == "disputed"]
    errors = [r for r in results if r.get("verdict") == "model_error"]
    blocked = [r for r in results if r.get("verdict") == "blocked"]
    unreadable = [r for r in results if r.get("verdict") == "no_source_text"]
    return {
        "layer": 3,
        "name": "主張站得住",
        "complete": sample_size is None,
        "population": len(eligible),
        "sampled": len(results),
        "judged": len(results) - len(errors) - len(blocked) - len(unreadable),
        "disputed": len(disputed),
        "model_errors": len(errors),
        "blocked": len(blocked),
        "no_source_text": len(unreadable),
        "review_status_mix": dict(status_mix),
        "results": results,
    }


def _claim_in_scope(
    payload: dict[str, Any],
    sources: SourceIndex,
    fragments: dict[str, dict[str, Any]],
) -> bool:
    """Whether this claim belongs to a source the current run covers.

    A claim names its source through `occurrences[].transcript_id`, which is a
    transcript title rather than a `source_id`, so the match goes through the
    source documents. Claims that name no source at all stay in: excluding them
    would quietly shrink the population on a technicality.
    """

    titles = {
        str(occurrence.get("transcript_id") or "")
        for occurrence in payload.get("occurrences") or []
    }
    titles.discard("")
    if not titles:
        return True
    for source_id, document in sources.documents.items():
        name = str(document.get("transcript_id") or document.get("title") or "")
        if name in titles and sources.covers(source_id):
            return True
    return False


def _step_fragment_ids(step: dict[str, Any]) -> list[str]:
    """一條證據步驟錨在哪幾個片段上。

    兩個欄位名並存，而且**複數的那個才是多數**：3,575 條步驟用
    `source_fragment_ids`，只有 166 條用單數的 `source_fragment_id`。只讀單數
    的後果不是少幾段——是第 3 層有 95% 的抽樣根本沒拿到原文，模型只能拿摘要
    去比摘要，而這一層存在的理由正是「直接讀原件」。
    """

    values: list[str] = []
    single = step.get("source_fragment_id")
    if single:
        values.append(str(single))
    for item in step.get("source_fragment_ids") or []:
        if item and str(item) not in values:
            values.append(str(item))
    return values


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
    claim_id: str,
    payload: dict[str, Any],
    evidence_lines: list[str],
    paragraphs: list[str],
    anchors: list[str] | None = None,
) -> str:
    source_block = "\n\n".join(f"【逐字稿全文】{p}" for p in paragraphs) or "（沒有可讀的原文段落）"
    if anchors:
        source_block += "\n\n【證據錨在原文的這幾句】\n" + "\n".join(f"· {a}" for a in anchors[:12])
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

教授在這篇裡說的全部話（證據錨在其中哪幾句，列在最後）：
{source_block}

注意：判斷「證據撐不撐得住」時，要看**整篇逐字稿**，不要只看錨點那一句。教授常常先鋪理由、後下結論，理由可能出現在錨點之前或之後很遠的地方。只有整篇裡都找不到，才算證據裡沒有。

---

用 JSON 回答。`reason` 用中文，一到三句，說清楚是哪一句撐不住。`quote` 填你據以判斷的原文或證據原句（沒有就留空）。"""


# ---------------------------------------------------------------------------
# 第 4 層：觀點歸併對
# ---------------------------------------------------------------------------


def audit_viewpoints(
    store: Store,
    sources: SourceIndex,
    model: IndependentModel,
    sample_size: int | None,
    rng: random.Random,
    workers: int = 8,
    progress: "Progress | None" = None,
    only: set[str] | None = None,
) -> dict[str, Any]:
    """抽樣問：判為同一個觀點的那幾條主張，真值條件是不是真的一致。

    這是 `ViewpointQualityReport` 問不出來的那一半。它的 `review_status` 固定是
    `system_verified`、`eligibility_decision` 只有 `pass/fail/partial_internal_only`
    ——查的是引用解不解析得開，不是歸併判得對不對。
    """

    viewpoints = {row["object_id"]: row["payload"] for row in store.collection("canonical_viewpoints")}
    revisions = {row["object_id"]: row["payload"] for row in store.collection("viewpoint_revisions")}
    claims = {
        row["object_id"]: row["payload"]
        for row in store.collection("claims", include_retired=True)
    }
    fragments = {
        row["object_id"]: row["payload"]
        for row in store.collection("source_fragments", include_retired=True)
    }

    links_by_viewpoint: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in store.collection("viewpoint_claim_links"):
        payload = row["payload"]
        if payload.get("effective_state") not in (None, "active"):
            continue
        if payload.get("link_type") not in MEMBER_LINK_TYPES:
            continue
        links_by_viewpoint[str(payload.get("viewpoint_id") or "")].append(payload)

    # 有一條成員主張就查。兩條以上才談得上「歸併對不對」，但一條也有它自己的
    # 問題：那一條主張的成分，真的和觀點的核心命題等價嗎？把它們排除，等於把
    # 剛建立、還只有一個來源的觀點全部跳過——而那正是最沒被看過的一批。
    candidates = [
        viewpoint_id
        for viewpoint_id, links in sorted(links_by_viewpoint.items())
        if links and viewpoint_id in viewpoints
    ]
    if only:
        candidates = [v for v in candidates if v in only]
    chosen = (
        candidates if sample_size is None else rng.sample(candidates, min(sample_size, len(candidates)))
    )

    def judge(viewpoint_id: str) -> dict[str, Any]:
        viewpoint = viewpoints[viewpoint_id]
        revision = revisions.get(str(viewpoint.get("current_revision_id") or ""))
        if revision is None:
            return {
                "viewpoint_id": viewpoint_id,
                "verdict": "disputed",
                "issue": "other",
                "reason": "current_revision_id 指不到任何 viewpoint_revision，無從判斷這個觀點主張什麼",
            }
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
        return {
            "viewpoint_id": viewpoint_id,
            "revision_id": viewpoint.get("current_revision_id"),
            "core_proposition": str(revision.get("core_proposition") or "")[:200],
            "linked_claims": len(links_by_viewpoint[viewpoint_id]),
            **verdict,
        }

    results = _judge_all(chosen, judge, workers, "觀點", progress)

    disputed = [r for r in results if r.get("verdict") == "disputed"]
    errors = [r for r in results if r.get("verdict") == "model_error"]
    blocked = [r for r in results if r.get("verdict") == "blocked"]
    return {
        "layer": 4,
        "name": "觀點歸併對",
        "complete": sample_size is None,
        "population": len(candidates),
        "sampled": len(results),
        "judged": len(results) - len(errors) - len(blocked),
        "disputed": len(disputed),
        "model_errors": len(errors),
        "blocked": len(blocked),
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
    for entry in layers.get(2, {}).get("struck_evidence_findings", []):
        items.append({
            "kind": "struck_evidence",
            "object_id": entry["object_id"],
            "verdict": entry["verdict"],
            "detail": f"{len(entry['struck_fragment_ids'])} 條引文只存在於劃掉的文字裡",
        })
    for entry in layers.get(2, {}).get("retired_evidence_findings", []):
        items.append({
            "kind": "retired_evidence",
            "object_id": entry["object_id"],
            "verdict": entry["verdict"],
            "detail": f"{len(entry['retired_step_ids'])}/{len(entry['evidence_step_ids'])} 條證據步驟已退役",
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
        if entry.get("verdict") == "no_source_text":
            items.append({
                "kind": "no_source_text",
                "object_id": entry["claim_id"],
                "verdict": "no_source_text",
                "detail": entry.get("reason", ""),
            })
        if entry.get("verdict") == "blocked":
            items.append({
                "kind": "not_judged",
                "object_id": entry["claim_id"],
                "verdict": "blocked",
                "detail": entry.get("reason", ""),
            })
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


def duplicate_source_documents(sources: SourceIndex) -> list[tuple[str, list[str]]]:
    """同一份逐字稿被登記成兩筆 `source_document`。

    `SRC-L3` 與 `SRC-2016_NYSC_3-f35be4755f9b` 是同一篇（五）3；`SRC-L4` 同理。
    早期 pilot 的那一筆沒有 `source_path`，它底下的片段也正是庫裡僅有的 40 條
    對不上的片段。重複本身無害，但兩筆記錄各自帶錨點，覆蓋率與去重都會把同一篇
    算兩次。
    """

    by_name: defaultdict[str, list[str]] = defaultdict(list)
    for source_id, document in sources.documents.items():
        if not sources.covers(source_id):
            continue
        name = str(document.get("transcript_id") or document.get("title") or "")
        if name:
            by_name[name].append(source_id)
    return [(name, sorted(ids)) for name, ids in sorted(by_name.items()) if len(ids) > 1]


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
        f"範圍        {meta['scope']}"
        + (
            f"（run ledger 說這一輪 ingest 成功的來源；另有 {meta['sources_out_of_scope']} 份更早批次的沒查。"
            "第 2 層例外：依賴是全庫的，範圍縮不了）"
            if meta["scope"] == "current-run"
            else "（庫裡全部來源，包含更早批次留下的）"
        ),
        "",
    ]
    if meta["duplicate_sources"]:
        lines.append("同一份逐字稿登記了兩次：")
        for name, ids in meta["duplicate_sources"]:
            lines.append(f"          {name[:40]}  {' · '.join(ids)}")
        lines.append("")

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
        struck_mix = Counter(e["verdict"] for e in layer.get("struck_evidence_findings", []))
        lines.append(
            f"          證據只在校對者劃掉的文字裡 {sum(struck_mix.values()):>4,}"
            + (f"   （{'、'.join(f'{k} {v}' for k, v in sorted(struck_mix.items()))}）" if struck_mix else "")
        )
        retired_mix = Counter(e["verdict"] for e in layer.get("retired_evidence_findings", []))
        lines.append(
            f"          主張引的證據步驟已退役 {sum(retired_mix.values()):>4,}"
            + (f"   （{'、'.join(f'{k} {v}' for k, v in sorted(retired_mix.items()))}）" if retired_mix else "")
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
        complete = layer.get("complete")
        lines.append(
            (
                f"第 3 層 主張全查     {layer['judged']:,} 條中 {layer['disputed']} 條有異議"
                if complete
                else f"第 3 層 主張抽樣     {layer['judged']} 條中 {layer['disputed']} 條有異議"
                f"   （母體 {layer['population']:,} 條）"
            )
        )
        mix = "、".join(f"{k} {v}" for k, v in sorted(layer["review_status_mix"].items()))
        lines.append(f"          抽樣的 review_status：{mix}")
        for entry in layer["results"]:
            if entry.get("verdict") == "disputed":
                lines.append(f"          [{entry['issue']}] {entry['claim_id']}")
                lines.append(f"              {entry['reason'][:88]}")
        if layer["model_errors"]:
            lines.append(f"          模型呼叫失敗 {layer['model_errors']}")
        if layer.get("blocked"):
            lines.append(
                f"          審計模型拒答 {layer['blocked']}   （安全過濾器擋下，這幾條沒有判讀）"
            )
        if layer.get("no_source_text"):
            lines.append(
                f"          取不到逐字稿 {layer['no_source_text']}   （片段沒記位置，沒送去判讀）"
            )
        lines.append("")

    if 4 in layers:
        layer = layers[4]
        complete = layer.get("complete")
        lines.append(
            (
                f"第 4 層 觀點全查     {layer['judged']} 個中 {layer['disputed']} 個有異議"
                if complete
                else f"第 4 層 觀點抽樣     {layer['judged']} 個中 {layer['disputed']} 個有異議"
                f"   （母體 {layer['population']} 個）"
            )
        )
        for entry in layer["results"]:
            if entry.get("verdict") == "disputed":
                lines.append(f"          [{entry['issue']}] {entry['viewpoint_id']}")
                lines.append(f"              {entry['reason'][:88]}")
        if layer["model_errors"]:
            lines.append(f"          模型呼叫失敗 {layer['model_errors']}")
        if layer.get("blocked"):
            lines.append(
                f"          審計模型拒答 {layer['blocked']}   （安全過濾器擋下，這幾條沒有判讀）"
            )
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
    parser.add_argument(
        "--claims",
        default="all",
        help="第 3 層查幾條主張：`all`（預設，範圍內全查）或一個數字（抽樣，改 prompt 時用）",
    )
    parser.add_argument(
        "--viewpoints",
        default="all",
        help="第 4 層查幾個觀點：`all`（預設）或一個數字",
    )
    parser.add_argument(
        "--workers", type=int, default=8, help="同時打幾個模型呼叫（預設 8）"
    )
    parser.add_argument(
        "--claim-ids",
        default="",
        help="只查這幾條主張（逗號分隔）。跟進某一條判定時用，不必為了一條等全查跑完",
    )
    parser.add_argument(
        "--viewpoint-ids", default="", help="只查這幾個觀點（逗號分隔）"
    )
    parser.add_argument("--seed", type=int, default=241, help="抽樣種子，同種子可重放")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="第 3、4 層用的獨立模型")
    parser.add_argument(
        "--scope",
        choices=("current-run", "all"),
        default="current-run",
        help=(
            "current-run：只查 run ledger 說這一輪 ingest 成功的來源（預設）。"
            "all：查庫裡全部來源，包含更早批次留下的"
        ),
    )
    parser.add_argument("--out", type=Path, default=None, help="報告輸出目錄")
    parser.add_argument(
        "--status-file",
        type=Path,
        default=None,
        help="把跑到哪裡了寫進這個檔案。預設寫在報告目錄底下，網頁就是讀那一份",
    )
    parser.add_argument(
        "--no-status-file", action="store_true", help="不要寫進度檔"
    )
    args = parser.parse_args(argv)

    wanted = {int(x) for x in args.layers.split(",") if x.strip()}
    settings = load_settings()
    database_url = settings.get("KNOWLEDGE_DATABASE_URL")
    if not database_url:
        raise SystemExit("KNOWLEDGE_DATABASE_URL 未設定")
    data_base_dir = Path(settings.get("DATA_BASE_DIR", ""))
    if not data_base_dir.is_dir():
        raise SystemExit(f"DATA_BASE_DIR 不是目錄：{data_base_dir}")

    reports_root = (
        data_base_dir / "wang-knowledge-platform/staging/reports/library-audit"
    )
    # 預設就寫。從命令列跑的那一輪如果不寫，網頁上就看不出有東西在跑——而看不
    # 出來的時候，人會以為它壞了，然後再按一次。
    status_path = (
        None
        if args.no_status_file
        else (args.status_file or reports_root / ".run-status.json")
    )
    progress = Progress(status_path)
    progress.update(
        state="running",
        pid=os.getpid(),
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        stage="讀取",
        done=0,
        total=0,
        scope=args.scope,
        model=args.model,
    )
    store = Store(database_url)
    documents = store.collection("source_documents")
    in_scope: set[str] | None = None
    if args.scope == "current-run":
        subjects = store.ingested_subjects()
        in_scope = {
            row["object_id"]
            for row in documents
            if str(
                row["payload"].get("transcript_id") or row["payload"].get("title") or ""
            ).replace("notes_manuscript:", "")
            in subjects
        }
    sources = SourceIndex(documents, data_base_dir, in_scope)
    rng = random.Random(args.seed)

    model: IndependentModel | None = None
    if wanted & {3, 4}:
        api_key = settings.get("GEMINI_API_KEY1", "")
        if not api_key:
            raise SystemExit("第 3、4 層需要 GEMINI_API_KEY1；或用 --layers 1,2 只跑確定性的兩層")
        model = IndependentModel(
            args.model,
            api_key,
            # 判讀結果按 prompt 存檔，跨次重用。全查一次一千多條，中途斷掉不必
            # 從頭付一次錢。
            cache_dir=data_base_dir
            / "wang-knowledge-platform/staging/reports/library-audit/.judgements",
        )

    layers: dict[int, dict[str, Any]] = {}
    if 1 in wanted:
        print("第 1 層 逐字對得上 …", file=sys.stderr)
        progress.update(stage="逐字對得上", done=0, total=0)
        layers[1] = audit_verbatim(store, sources)
    if 2 in wanted:
        print("第 2 層 覆蓋誠實 …", file=sys.stderr)
        progress.update(stage="覆蓋誠實", done=0, total=0)
        layers[2] = audit_coverage(store, sources)
    claim_limit = None if args.claims == "all" else int(args.claims)
    viewpoint_limit = None if args.viewpoints == "all" else int(args.viewpoints)
    if 3 in wanted and model is not None:
        print(
            f"第 3 層 主張（{'全查' if claim_limit is None else f'抽 {claim_limit} 條'}）…",
            file=sys.stderr,
        )
        layers[3] = audit_claims(
            store,
            sources,
            model,
            claim_limit,
            rng,
            args.workers,
            progress,
            {x.strip() for x in args.claim_ids.split(",") if x.strip()} or None,
        )
    if 4 in wanted and model is not None:
        print(
            f"第 4 層 觀點（{'全查' if viewpoint_limit is None else f'抽 {viewpoint_limit} 個'}）…",
            file=sys.stderr,
        )
        layers[4] = audit_viewpoints(
            store,
            sources,
            model,
            viewpoint_limit,
            rng,
            args.workers,
            progress,
            {x.strip() for x in args.viewpoint_ids.split(",") if x.strip()} or None,
        )

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": args.model if model else "未使用",
        "seed": args.seed,
        "scope": args.scope,
        "sources": len(sources.in_scope) if sources.in_scope is not None else len(sources.documents),
        "sources_out_of_scope": len(sources.out_of_scope),
        "duplicate_sources": duplicate_source_documents(sources),
        "fragments": sum(
            1
            for row in store.collection("source_fragments")
            if sources.covers(str(row["payload"].get("source_id") or ""))
        ),
        "claims": len(store.collection("claims")),
        "viewpoints": len(store.collection("canonical_viewpoints")),
    }
    queue = build_human_queue(layers)
    report = render_report(layers, meta, len(queue["items"]))

    out_dir = args.out or (
        reports_root / datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
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

    progress.update(
        state="finished",
        stage="完成",
        run_id=out_dir.name,
        run_dir=str(out_dir),
        finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    print(report)
    print(f"\n輸出：{out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
