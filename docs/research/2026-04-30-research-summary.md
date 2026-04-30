# Research Summary — 2026-04-30

研究來源：三個並行 subagent (general-purpose) 於 2026-04-30 執行，each ~5-10 min compute。
研究範圍對齊 `tasks/todo.md` 中的 13 個 topics。

本文件僅濃縮**會直接影響架構決策**的關鍵發現。完整研究見對話紀錄與 `prompts/` 目錄。

---

## A. SEC 10-K 領域（Task 3 主力）

### A1. 結構（必須寫進 spec 的硬事實）
- 官方 10-K Item structure：**Items 1, 1A, 1B, 1C, 2, 3, 4 (Part I) / 5, 6, 7, 7A, 8, 9, 9A, 9B, 9C (Part II) / 10, 11, 12, 13, 14 (Part III) / 15, 16 (Part IV)**
- **時間軸**（eval set 必須跨越這些）：
  - Item 1A Risk Factors：**2005-12-01** 起
  - Item 1B Unresolved Staff Comments：2005-12-01 起（accelerated/large accelerated/WKSI）
  - Item 9C HFCAA：**2022-01-10** 起
  - Item 1C Cybersecurity：**FY ending on/after 2023-12-15** 起強制
  - Item 6 → `[Reserved]`：**2021** 起
- iXBRL 強制：2019-06-15（large accelerated GAAP）→ 2021-06-15（全面）
- HTML 不是某年強制，而是 1996 起逐漸取代 SGML/.txt

### A2. 「Submissions API 沒有 item-level metadata」
**核心難點**：`data.sec.gov/submissions/CIK*.json` 中的 `items[]` 是 8-K trigger，不是 10-K item structure。**所有 item-level 結構必須從文件 body 解析**。

### A3. 既有工具盤點
| 工具 | 評估 | 與我們 spec 的 gap |
|---|---|---|
| **edgartools** v5.30.2 (active 2026-04-29) | MIT, 最完整 | 無 `char_range`、無 `status` 分類、ABS 10-K 不支援、pre-2002 .txt 部分支援 |
| sec-api.io | 商業 SaaS | 閉源、付費、不可審計 |
| alphanome-ai/sec-parser | 通用 semantic tree | 不 label Item 編號、不 classify by-reference |
| john-friedman/sec-parsers | section finding by title | 無 char offsets、無 by-reference detection |
| secedgar / sec-edgar-api | filing fetcher | 不 parse |

**結論**：用 edgartools 為 segmentation backbone，自加 (a) char-offset 重對齊、(b) status classifier、(c) `[Reserved]` / "Not applicable" / cover-page 處理。

### A4. Incorporated by reference 偵測
- **Cover-page 區塊**：必查 pre-Item 1 的 "DOCUMENTS INCORPORATED BY REFERENCE" 段落，提到的 proxy year + 120-day window 寫入 metadata。
- **Per-item regex**：
  ```
  (?i)\b(information\s+(required\s+(by\s+this\s+[Ii]tem\s+)?|called\s+for\s+by\s+this\s+[Ii]tem))\b.{0,200}\b(incorporated\s+(herein\s+)?by\s+reference|will\s+be\s+(included|set\s+forth)\s+in\s+the\s+(\d{4}\s+)?[Pp]roxy\s+[Ss]tatement)\b
  ```
- **Negative control**："The information contained on the websites referenced in this Form 10-K is not incorporated by reference into this filing." → 不可誤判。
- **混合情況**：Apple 2024 Item 10 = inline (insider trading policy) + by-reference → 需要 `partial` status 或 sub-segments。

### A5. Pathological filing catalog（已驗證 CIK + accession）
13 個範例已建檔，涵蓋：
- 1995 SGML/.txt 銀行 (Chemical Banking, CIK 19617)
- 2019 pre-iXBRL 大型 conglomerate (Berkshire CIK 1067983)
- 2021 GE cross-ref TOC (CIK 40545)
- 2020/2022 Intel 大檔 (CIK 50863)
- 2023 vs 2024 Apple (Item 1C 邊界, CIK 320193)
- 2024 Goldman 10-K/A Part III only (CIK 886982)
- 2024 ABS 10-K John Deere Owner Trust (CIK 1816589, 用 Reg AB schema)

### A6. EDGAR API rate limit & header
- 10 req/sec hard limit, IP-based, 三個域名（data/www/efts）共享，**violation 通常 block 10 min**
- `User-Agent` 必填，格式 `"Sample Co AdminContact@samplecompany.com"`
- Full-text search 只覆蓋 2001 起

---

## B. Browser Agent 領域（Task 2）

### B1. SOTA 從「prompt + 截圖」走向「Planner-Actor-Validator + Hybrid state」
- **Skyvern 2.0**：85.85% on WebVoyager via Planner-Actor-Validator + replan loop
- **Stagehand v3**：cached selector + LLM fallback（最容易抄、SOTA 模式）
- **Browser-Use**（91k stars）：DOM clickable index + 截圖 + a11y tree hybrid

### B2. Self-correction 真實實作（不是 try/except retry）
1. **Planner-Actor-Validator 三段架構**（核心）
2. **Reflexion 自反思記憶** — 失敗 trajectory 寫入 SQLite，下次 retrieve top-k
3. **DOM mutation diff**：action 後 DOM/URL/screenshot 都沒變 → silent no-op
4. **Multi-strategy fallback**：visual / DOM / keyboard / search box → 連續失敗升級
5. **LLM 條件驗證 (ProCo)**：把任務關鍵條件遮蔽，從 trajectory 推回去驗證

### B3. Self-maintenance / Self-healing selector
- **十層 locator 階梯**：getByRole → data-testid → ARIA → CSS → 可見文字 → XPath → 視覺
- **語意 selector + LRU cache**：cache key = (page_url_template, intent)；DOM hash 變更則 invalidate；持久化到 disk → **跨 session 證明 self-maintenance**
- **Healenium 風格 multi-attribute fingerprint**：weighted LCS 在新 DOM 找最相似

### B4. Eval set 設計建議
- **規模 30-40 任務、6 domain、3 難度**
- Domain：(1) 電商搜尋/篩選 (2) 維基/文件查詢 (3) 旅遊/地圖訂位 (4) 表單填寫 (5) 多步比價 (6) **SEC/政府網站結構化抓取（finance pack）**
- 評分：exact-match + LLM-as-judge (WebVoyager 風格 prompt) + manual spot-check
- 反過擬合：同任務 3 種 paraphrase + 同網站 mobile/desktop
- 必填 negative oracle：「若 agent 報成功但這條件未滿足則記 fail」
- 預算：step cap 30、token cap 50k、wall-clock cap 3 min

### B5. Silent failure 偵測組合（最划算組合）
1. **DOM mutation diff** (cheap, 高信心)
2. **URL + page title diff** (cheap, 高信心)
3. **Network side-effect 檢查** (cheap, 高信心)
4. **LLM trajectory verifier** (expensive, 只在前三 detect 到 anomaly 時才呼叫)
5. **Negative oracle**：每任務必填「期待出現」斷言

### B6. Zeabur + headless browser **可行**
- Browserless 模板已存在（763+ 部署），證實 Chromium on Zeabur 可運行
- Pro plan ($19/mo, 4 vCPU / 16 GB) 足夠
- 坑：`/dev/shm` 預設 64 MB，需要 `--shm-size=2gb` 或 `--disable-dev-shm-usage`

### B7. **Browser agent 不需要 SEC**
- DEF 14A 在 EDGAR 上是標準 form，純 API 取得
- 強行整合會被看穿
- **採取折衷**：題二保持 generalized，但 ship 一組 finance-domain task pack（IR earnings deck、news 驗證 risk factors）

---

## C. Claude Skills + Zeabur（Task 1 + 平台層）

### C1. SKILL.md 規格
```yaml
---
name: my-skill                        # ≤64 chars, lowercase + hyphen
description: <≤1024 chars>            # required
allowed-tools: Read Grep Bash(git *)  # optional, Claude Code only
disable-model-invocation: false       # optional
arguments: [issue, branch]            # optional
when_to_use: <trigger phrases>        # optional
---
# Markdown body
```

### C2. Skill 觸發機制（核心）
- **純 LLM 文本推理**，沒有 embedding ranking、沒有 keyword regex
- description 是搜尋查詢的目標文本
- 排版優先：description+when_to_use 在 listing 中**截至 1,536 chars**

### C3. 好 description 五條原則
1. 第三人稱、現在式、動詞為主（"Extracts ..."）
2. 公式：`[動詞] [object] [output] + Use when [trigger phrases including synonyms]`
3. front-load 關鍵 use case
4. **Pushy 對抗 undertrigger**："Make sure to use this skill whenever ..."
5. 同義詞與 intent phrasing — 寫使用者實際會說的話

### C4. 如何 TEST trigger accuracy（評分點）
**內建 eval harness 是 deliverable**：
- 每個 skill 一個 `eval.json`，含 `should_trigger[]` + `should_not_trigger[]`，每個 query 跑 3 次
- 60/40 train/test split，5 輪 iterate description
- 指標：trigger rate (TPR) + false trigger rate (precision)

### C5. Anthropic 自家 skills 沒有 CI/CD
最接近的是 `webapp-testing`、`mcp-builder`。Trail of Bits 偏 security audit。
→ **Task 1 brief 是新領域，沒有現成抄襲對象**，boundary 設計是評分點。

### C6. CI/CD skill boundary 推薦（4 個 skill）
| Skill | Input | Output | Idempotency |
|---|---|---|---|
| `lint-and-test` | repo_path / pr_number | `{passed, lint, tests}` | cache key = SHA + lockfile hash |
| `build-and-release` | version, target, dry_run | `{artifact_url, digest}` | content-addressed, `disable-model-invocation: true` |
| `dependency-audit` | repo_path, severity_threshold | SARIF or JSON | cache by lockfile hash + advisory DB date |
| `security-scan` | repo_path, scan_types | SARIF + risk_score | read-only, cache by SHA |

### C7. Zeabur 能力矩陣（決策關鍵）
- ✅ Docker / Buildpack
- ✅ Postgres / MySQL / Redis（一鍵）
- ✅ Persistent volumes（**但啟用後失去 zero-downtime deploy**）
- ✅ Headless Chromium (Pro plan, --shm-size 設定)
- ⚠️ No first-class cron primitive → 用 Inngest cron 或 supercronic
- ⚠️ HTTP request timeout (low confidence) → 長任務一律走 worker + queue
- ❌ No native S3 → 自部 MinIO 或外接 R2/S3
- 計費：$0.00025/GB-min memory（AWS parity，無 markup）；Free $5/mo credit

### C8. 推薦平台架構（FastAPI + Inngest + Postgres + Redis + 三種 worker）
- Gateway: FastAPI（auth + idempotency-key + skill lookup）
- Workflow: **Inngest self-hosted**（單一 binary + Postgres，比 Temporal 輕）
- Workers: ci / extractor / browser，各自的 Docker image 與 RAM 配額
- Postgres: run history + skill manifest + audit log
- Redis: idempotency cache + dedup
- Volume: `/cache` for git clone、`/profiles` for browser session

預估 idle cost ~$5-8/mo，browser worker pay-as-you-go。

---

## D. 整合敘事（基於上述發現修正）

**原方案 A（SEC AI Analysis Platform）有缺陷**：browser agent 沒有自然的 SEC use case。

**修正版 A'**：
1. **Task 1 = 平台層**（dogfood 真實）
2. **Task 3 = 整合 anchor**（最深、評分主軸）
3. **Task 2 = 通用 browser agent**，但內建 finance-domain task pack 證明 transferability：
   - IR 網站 earnings deck 抓取
   - 用 news/Wikipedia 驗證 10-K risk factors 是否實現
   - Form 144 / 第三方持股資料庫互查
4. README 主敘事：「Skills 平台是基礎建設，三題都跑在上面，三者透過平台統一 observability + idempotency + auth + scheduling」

這個敘事 honest、面試官打不穿、且每題仍可獨立 deploy。

---

## E. 共通 Open Design Questions（spec 必須回答）

### Task 3
1. `char_range` 的 source string 是 raw HTML / cleaned text / both？（推薦 both）
2. Hybrid items 如何呈現？（推薦 `partial` status + 兩個 child segments）
3. ABS 10-K 處理？（推薦 `status: non_standard`，不在主 schema 中）
4. 10-K/A 與原始 10-K 的關係？（推薦獨立 parse，metadata 標 `amends: <accession>`）
5. Cover-page 是 synthetic record 還是 metadata？（推薦 `item_number: "cover"`, `part: 0`）
6. Eval gold labels：人工 ≥3 / silver ≥7

### Task 2
1. Vision-first 或 DOM-first？（推薦 DOM-first + vision fallback = Stagehand v3 模式）
2. Validator 跟 Actor 同模型不同 prompt（7 天版 OK）
3. Selector cache TTL：DOM hash invalidate + LRU 1000 entry
4. Captcha 策略：graceful fail with reason
5. 是否跑 WebVoyager 子集 (10 task) 當 external validation？（**強烈建議**，~$2-5）

### Task 1
1. Skills as filesystem source-of-truth + manifest scanner sync to Postgres
2. Auth：API key（rotate via env）+ GitHub fine-grained PAT
3. 各 skill timeout：先實測 Zeabur HTTP，**所有 invoke 一律 async**
4. Eval harness 作為 deliverable（評分點直擊）
