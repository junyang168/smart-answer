---
name: chapter-slides
description: 做一章马太福音的逐節聆聽页面（NYSC 系列讲座）：选堂、写进 chapters.json、找出这一章的释经段落、写同步幻灯、审阅修正、起章标题，把结果和留给负责人的意见报告出来，并在方案文档记一节。负责人说「做太 21」「/chapter-slides 太 21」时用。Claude Code 与 Codex 共用这一份。
---

# 按章逐節聆聽页面

设计见 `docs/wang-knowledge-platform/30-authoring/matthew_chapter_exposition_page_solution.md`（下称「方案」）。§4.1 是加一章的步骤，§4.6 是幻灯的规则，§6 是每一章跑过的记录。本 skill 只是把那里的步骤按次序跑完，不另立规则；规则改了改方案和这里，走 PR。

所有命令在仓库根目录跑，用 `backend/.venv/bin/python`，前面加 `PYTHONPATH=.`。`DATA_BASE_DIR` 和知识库连接从仓库的 `.env` 读。下面的命令用 `$D` 指它，先设好：

```bash
export D=$(grep -o '^DATA_BASE_DIR=.*' .env | cut -d= -f2)
```

**固定的边界：**

- 只做 NYSC 马太福音系列讲座这一个来源。母本、文章、CVP 都不在这里。
- 章的资料在 `$D` 里，页面下一个请求就有，**不部署**。
- 审阅最多两轮修正，**不加轮次**；用完轮数的意见原文交给负责人，不自己改幻灯文字。
- `chapter_exposition` 已经跑过的章，**不重跑**，除非负责人要求。
- 代码改动（修书名表之类）在这一章所属的卡和 worktree 里做，见仓库 `AGENTS.md`。

## 0. 先看有没有人在跑

```bash
ps -ax -o pid,command | grep -E "chapter_(exposition|slides|title)" | grep -v grep
```

同一章已有跑次就停，告诉负责人，不并行。

## 1. 这一章在不在清单里

```bash
python3 -c "import json;d=json.load(open('$D/chapter_exposition/chapters.json'));print([c['passage'] for c in d['chapters']])"
```

已在清单、`$D/chapter_exposition/<passage>/` 也有每一堂的结果 → 跳到第 3 步。

## 2. 加一章（方案 §4.1）

**选系列。** 方案 §4.1 的表写着每个系列讲哪几章。表里没有这一章，就在逐字稿里找（下一段），找到了把表补上。

**选堂。** 按逐字稿里念到的章号，连同前后一堂：

```bash
python3 - "2018 NYSC 專題：馬太福音釋經（七）" 二十一 <<'EOF'
import json, os, re, sys, glob
series, chapter = sys.argv[1], sys.argv[2]
for f in sorted(glob.glob(f"{os.environ['D']}/script_published/{series}*.json")):
    d = json.load(open(f)); s = d["script"] if isinstance(d, dict) else d
    text = " ".join(x.get("text", "") for x in s if isinstance(x, dict))
    print(f.rsplit("/", 1)[-1], len(re.findall(rf"(?:第\s*)?{chapter}\s*章", text)))
EOF
```

**查主张有没有 refresh。** 留不留由模型读逐字稿判断，主张只作提示；太 8–15、5–7 是等系列的主张都 refresh 了才做的。查各堂主张的 `review_status`：

```bash
psql smart_answer_knowledge -At -F' | ' -c "
select split_part(substr(e.payload->'source_fragment_ids'->>0,4),'-DK-',1), c.review_status, count(distinct c.object_id)
from wang_knowledge.objects c, jsonb_array_elements_text(c.payload->'evidence_step_ids') eid
join wang_knowledge.objects e on e.object_id=eid and e.collection='evidence_steps'
where c.collection='claims' and c.retired_at is null
  and e.payload->'source_fragment_ids'->>0 like 'FR-2018_NYSC_%'
group by 1,2 order by 1,2"
```

（`FR-2018_NYSC_%` 换成这个系列的片段前缀。）还有 `candidate` 或 `human_review_required` 的，**先问负责人**是现在做还是等 refresh。

**写进清单。** 先备份，再加一条：经文 `mat-<章>-1-<末节>`、来源「紐約靈命進深會」、系列、选出的堂、和合本的分段（`$D/bible/cuv/MAT/<章>.json` 是经文本身，分段照和合本的段落标题；分段必须从第 1 节接到最后一节，否则程序跳过这一章）。

```bash
cp -p $D/chapter_exposition/chapters.json $D/chapter_exposition/chapters.json.bak-$(date +%Y%m%d-%H%M%S)
```

把要加的那一条给负责人看一眼再写。

**找释经段落：**

```bash
PYTHONPATH=. backend/.venv/bin/python -m backend.pipeline.chapter_exposition mat-21-1-46
```

每堂一行结果，`kept_minutes` 是保留的分钟数。**保留 0 分的那一堂从清单里拿掉**（方案 §4.1 第 2 步）。

## 3. 写幻灯

```bash
PYTHONPATH=. backend/.venv/bin/python -m backend.pipeline.chapter_slides mat-21-1-46
```

- `saved`：存档。`unshown_references` 是程序在逐字稿里认出、幻灯上没有的经文，只列不废，报告里提一句。
- `rejected`：程序核对不过，整堂不用。**先查原因再动**，看 `problems`：
  - 程序这边的问题（如书名表不认得和合本写法：太 12 的「約翰壹書」、太 21 的「列王紀上」）→ 修代码、补测试，然后**用同一份回答重核，不再调模型**：

    ```bash
    PYTHONPATH=. backend/.venv/bin/python -m backend.pipeline.chapter_slides mat-21-1-46 --lecture （七）2 --recheck
    ```

    已经存过的那一堂（`already_saved`）不会被旧的拒收稿盖掉；决定或提示词变了（`decisions_changed`、`prompt_changed`）也不重核。
  - 回答本身的问题（幻灯没从第一句开始、要点不在这一张里……）→ 去掉 `--recheck` 重跑这一堂，会再调一次模型。同一堂第二次还被拒，停下来告诉负责人。
- `nothing_kept`：这一堂没保留片段，应该已在第 2 步从清单拿掉。

### 本堂 Claims 提示试验（负责人明确指定时）

方案 §4.6 的单堂试验：仍由逐字稿分张、定时，Claims 和证据步骤只提示重点。先用 `backend.pipeline.chapter_slide_claims <passage> --lecture '<全名>' --output <提示包.json>` 只读编译，程序核实际逐字稿与已存选材一致、摘录唯一定位及保留句子；不重跑选材。包内披露候选状态及排除清单。 编译时从 SourceFragment 对应的保留句预计算每条 Claim 的连续证据范围，按证据作用区分，遇省略片段或证据空缺就断开。生成、修订及审阅收到范围与原文，再逐条选教授引入该要点的句子。

复制这一堂原选材到隔离 `<data>/chapter_exposition/<passage>/`，保存旧幻灯和全部旧审阅供比较。生成时给 `chapter_slides` 加 `--claims-context <提示包.json> --data-dir <data>`；审阅用同一个 `--data-dir`，程序从幻灯存档读取冻结包，不重新取库。最多两轮修正；每条 Claim 都须有审阅处置，原文不支持的不能补进来。隔离调用保存精确请求与原始回答，未知调用不自动重试。

报告披露旧稿／试稿、重点回查、剩余意见和是否替换线上稿；逐条回查不冒充已建立正式 eval，也不以幻灯数量增加断言效果改善。单堂试验不重写整章标题。

**隔离试稿与正式数据是两个步骤。** 负责人要求把试稿放到正式页时，运行 `PYTHONPATH=. backend/.venv/bin/python -m backend.pipeline.chapter_slides_publish <试跑目录>`；有负责人逐字指定的标题修订时另加 `--owner-corrections <修订.json>`。入口核对选材、实际逐字稿、原稿和最新非阻断审阅的 SHA，备份原正式资料，再写入同一堂的正式数据；保留模型原答、审阅及负责人修订的区别。不调用模型、不重置轮次、不因部署代码就声称数据也已发布。必须从正式网页实际请求的 `/api/public/original-audio/<passage>` 核对标题和要点。

要点的句子编号从本张原文开头核对，标教授开始引入这条要点的位置。不要绑到后面的重复句或讲完后的归纳句。画面在当前录音片段内把换张和要点提前一秒，当前讲述的要点亮到下一条的开讲时间。核对负责人指出的时序问题时检查全堂，验证仅改时间锚点的稿件文字一致，保存前后 SHA 与原文依据。生成、修订、审阅使用同一份规则。程序会拦截同张连续原文中绑到定义句的术语要点。审阅确认的过早或过晚锚点列为「出現時間」，发布前须修正。

标题须涵盖每张完整原文与全部要点的共同主题（方案 §4.6），不能只取一条 Claim 或一个子段。负责人指出这类问题时，修生成、修订、审阅共用规则并保存原文反例，不以预览标题替换表充当系统修复；不因此自动增加原试跑的调用轮次。

## 4. 审阅、修正、章标题

```bash
PYTHONPATH=. backend/.venv/bin/python -m backend.pipeline.chapter_slides mat-21-1-46 --review
PYTHONPATH=. backend/.venv/bin/python -m backend.pipeline.chapter_title mat-21-1-46
```

审阅自己循环：审 → 修 → 再审，直到没有意见或两轮用完（`rounds_used_up`）。每轮的审阅和修正稿存在 `$D/chapter_slides/<passage>/` 的 `*.review-N.json`、`*.revision-N.json`。

## 5. 报告

打开页面确认有这一章：`https://dallas-hlc.org/resources/wang-repository/books/mat/<章>`。

给负责人一张表，格式与方案 §6 相同：

| 讲道 | 保留 | 幻灯 | 初审 → 终审 | 留给负责人的意见 |
| --- | ---: | ---: | --- | ---: |

数字从文件读：保留分钟是 `chapter_exposition` 的输出；幻灯数是存档的 `slides` 条数；等级是第一份和最后一份 `review-N.json` 的 `grade`（标出 `blocking`）；意见是最后一份审阅的 `findings`。

**意见原文给负责人**，分清哪条是经文或忠实方面的、哪条是跟得上或完整方面的。不自己改，不再加一轮。

## 6. 记录

在方案 §6 加一小节（`### 6.N 太 <章>`），写：负责人哪天怎么说的、主张状态、选了哪几堂和为什么拿掉某一堂、上面那张表、被拒的原因和怎样处理、留给负责人的意见概要。目录、状态方框的日期一起改。跑 `python3 scripts/check-docs.py docs`。在这一章所属的卡上提交，用 `scripts/wrap-up.sh` 开 PR。
