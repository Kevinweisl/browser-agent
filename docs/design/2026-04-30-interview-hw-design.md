# AI Coding Test 2026 — Design Spec

**作者**：Kevin Wei
**日期**：2026-04-30
**Deadline**：2026-05-07（面試前一日）
**目標等級**：A 級
**時間預算**：7 天 × 8-12 小時/天 ≈ 56-84 小時

---

## 0. 目標與約束

### 0.1 評分目標（A 級）
- Eval 設計有深度
- 系統能展現分層與權衡
- 失敗模式誠實
- Prompt 紀錄看得出高品質的 AI 協作

### 0.2 共通約束（題目硬性要求）
1. AI-first workflow（Claude Code preferred；Skills 加分）
2. 公開 Git repo，commit history 反映真實開發過程
3. **Zeabur 部署**所有題目為公開可存取服務
4. `prompts/` 資料夾保存主要 prompt
5. README 寫明：如何執行、設計決策、AI 協助環節
6. 使用公開或自建資料

### 0.3 評估方式
面試官會用 **held-out 資料**對 deployed system 實測 → **三題都不能只做 happy path**。

---

## 1. 整體策略

### 1.1 為何不做「四題（A+B+C+D）並列」
- 7 天 × 10h ≈ 70h；三題各做到 A 級保守需 70-100h，再加 D 必爆預算
- D 題不在題目要求中，自加 deliverable 反而傳達「不懂優先順序」
- Held-out 實測時，整合敘事不加分

### 1.2 採取的策略：**深做 1 主力 + 1 平台 + 1 輔助 + 統一整合敘事**
- **Task 3（10-K 抽取）= 主力深做**（用戶最強領域：NLP / 文件抽取）
- **Task 1（Skills 平台）= 平台層 dogfood**（Task 2、Task 3 都跑在上面）
- **Task 2（Browser Agent）= 通用 + finance pack**（獨立題、附帶金融域 task 證明 transferability）

### 1.3 整合敘事（修正版）
研究確認 browser agent 對 SEC 沒有自然 use case（DEF 14A 在 EDGAR 上是標準 form）。**強行整合會被看穿**。

修正後敘事：
> 「我建造了一個 Claude Skills 平台，三道題目都實作為跑在這個平台上的 production skill。Task 1 的 skill design 是真實 dogfood 過的；Task 3 是平台上最深、最對齊我強項的 skill；Task 2 是通用 browser agent，附帶一組 finance-domain task pack（IR 網站 earnings deck、news 驗證 10-K risk factors）證明對特定領域的 transferability。每題仍各自有獨立 Zeabur endpoint，可被 held-out 測試獨立打。」

---

## 2. 系統架構

### 2.1 高層架構圖（**ARCHITECTURE UPDATE 2026-04-30**：簡化為 Postgres-only）

```
┌──────────────────────────────────────────────────────────────────┐
│                         User Surfaces                            │
│  Web UI (Next.js)  │  Claude Code (local CLI)  │  External API   │
└──────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│              Local: bare-metal Python    /  Zeabur: Buildpack    │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Gateway / API (FastAPI, uvicorn)                          │  │
│  │  - POST /skills/:name/invoke (Idempotency-Key header)      │  │
│  │  - GET  /runs/:id, /runs/:id/stream (SSE)                  │  │
│  │  - GET  /skills (manifest list)                            │  │
│  │  - Auth: API key in header                                 │  │
│  └─────────────────────┬──────────────────────────────────────┘  │
│                        │ INSERT runs(status='queued')            │
│                        ▼                                         │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Postgres (single source of truth)                          │  │
│  │  - skills (manifest)        - runs (queue + history)       │  │
│  │  - audit_events             - selector_cache               │  │
│  │  - idempotency: UNIQUE (skill_name, idempotency_key)       │  │
│  │  - dispatch: SELECT ... FOR UPDATE SKIP LOCKED             │  │
│  └────────────────────────┬───────────────────────────────────┘  │
│                           │ poll-and-lock                        │
│             ┌─────────────┼─────────────┐                        │
│             ▼             ▼             ▼                        │
│        ┌────────┐   ┌──────────┐  ┌──────────┐                   │
│        │  W-CI  │   │ W-EXTRA  │  │ W-BROW   │                   │
│        │(local  │   │(local OR │  │(Zeabur   │                   │
│        │ proc)  │   │ Zeabur)  │  │ Pro/Dev) │                   │
│        └────────┘   └──────────┘  └──────────┘                   │
└──────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
                  ┌─────────────────────────┐
                  │  External services      │
                  │  - GitHub API           │
                  │  - SEC EDGAR API        │
                  │  - Anthropic API        │
                  └─────────────────────────┘
```

### 2.2 為什麼是 Postgres-only（取代原 Inngest + Redis）

研究階段曾推薦 Inngest（self-hosted）+ Redis。執行前重新檢視：對 7 天 solo project，**Postgres 一個就夠**：
- **Job queue**：`SELECT ... FROM runs WHERE status='queued' FOR UPDATE SKIP LOCKED LIMIT 1` 是經典 pattern
- **Idempotency**：`UNIQUE (skill_name, idempotency_key)` 即時去重
- **Step memoization**：`runs` row 自身的 status state machine + JSONB result_json 已足夠
- **Selector cache**：`selector_cache` table（已在 schema 中）
- 移除 Inngest + Redis = 少 2 個服務、少 2 套 SDK、少 2 種 failure mode

### 2.3 Local 開發 vs Zeabur 部署

| 環境 | 怎麼跑 |
|---|---|
| **Local dev** | `brew install postgresql@16` + conda env (Python 3.12) + `uvicorn` + python worker process。**沒有 Docker、沒有 Redis、沒有 Inngest**。setup 腳本 idempotent，README 一行 command 重現環境。 |
| **Zeabur** | 預設用 Buildpack auto-detect (從 `pyproject.toml`)；Postgres 用 marketplace 一鍵；browser worker 因 Playwright 需要明確 base image，寫一個 `Dockerfile.browser`（其他 service 不寫 Dockerfile） |

| Worker | Local | Zeabur |
|---|---|---|
| `worker-ci` | `python -m worker_ci` | Buildpack |
| `worker-extractor` | `python -m worker_extractor` | Buildpack |
| `worker-browser` | `python -m worker_browser`（需先 `playwright install chromium`） | `Dockerfile.browser` based on `mcr.microsoft.com/playwright:v1.x-noble` |

**部署方案**：
- **Local 開發**（Day 1-3）：bare-metal Postgres + Python，`scripts/dev.sh` 一鍵啟動
- **Zeabur 部署**（Day 4 開始）：Buildpack 為主，browser worker 用 Dockerfile
- **Pro plan ($19/mo)** 只在 Day 6/7 browser worker 實測 OOM 時才升級
- 預估總成本：$5（最差 $19，整個專案週期）

### 2.4 Skill 呼叫流程（端到端）

1. UI / CLI 送 `POST /skills/sec-extract-10k/invoke` + body `{cik: 320193, accession: "0000320193-24-000123"}` + header `Idempotency-Key: <uuid>`
2. Gateway 查 `runs` 表 unique index → 命中回 cached `run_id`；否則：
3. Gateway 寫 row 進 `runs` table（`status='queued'`），回 `{run_id, status: queued}`
4. Worker process 持續 poll `runs WHERE status='queued' AND worker_target=<self>` (FOR UPDATE SKIP LOCKED)
5. Worker 取得 row 後 `UPDATE status='running'`，執行 skill logic
6. 結果寫回 Postgres：`UPDATE runs SET status='completed', result_json=..., completed_at=now()`
7. UI 用 SSE 訂閱 `/runs/:id/stream` 看 status 變化

### 2.5 Reproducibility（環境可重現）

題目要求公開 repo 給面試官看。**任何人 clone 後 5 行 command 內可跑通本地測試**：

```bash
# Prerequisites: brew, conda, Python 3.12 via conda
git clone <repo>
cd interview_hw
./scripts/setup.sh           # creates conda env, installs deps, starts postgres, runs migrations
export ANTHROPIC_API_KEY=...
./scripts/dev.sh             # starts gateway + workers
./scripts/test.sh            # runs all pytest suites
```

依賴鎖定：
- `pyproject.toml` 為 single source of truth
- `uv.lock`（或 `requirements.lock`）為精確版本鎖
- `migrations/00*.sql` 順序編號 + idempotent
- `.env.example` 列所有必要環境變數

---

## 3. Task 1 — Claude Skills CI/CD 平台

### 3.1 範疇
打造 **Skills 平台** + **4 個 CI/CD skill** + **trigger eval harness**。
平台同時宿主 Task 2、Task 3 的 skills。

### 3.2 Skill 切分（4 個）

#### 3.2.1 `lint-and-test`（合併不拆，brief 暗示）
```yaml
---
name: lint-and-test
description: |
  Runs the project's linter and test suite against a given branch or pull
  request, returns a unified pass/fail report with file-level findings. Use
  when the user wants CI checks on a PR, asks "does this break tests?",
  needs lint+test before merge, or mentions GitHub Actions for quality gates.
allowed-tools: Bash(git *) Bash(npm *) Bash(pnpm *) Bash(pytest *) Bash(ruff *) Read Grep
---
```
- **Input**: `{repo: str, ref: str, language_hint?: str}`
- **Output**: `{passed: bool, lint: {findings: [{file, line, severity, message}]}, tests: {passed: int, failed: int, errors: [...]}}`
- **Idempotency**: cache key = SHA + lockfile hash；重跑不重 install dep

#### 3.2.2 `build-and-release`
```yaml
---
name: build-and-release
description: |
  Builds production artifacts (Docker image, npm package, or zip) and
  optionally publishes them to a registry with semantic version tagging.
  Use when the user asks to ship, publish, cut a release, push to registry,
  or tag a new version after CI passes.
disable-model-invocation: true
allowed-tools: Bash(docker *) Bash(npm publish) Bash(git tag *) Bash(gh release *)
---
```
- **Input**: `{repo, version, target: docker|npm|pypi|github-release, dry_run: bool}`
- **Output**: `{artifact_url, digest, version, registry}`
- **Idempotency**: tag 已存在且 digest match → noop；`dry_run=true` 預設 retry-safe

#### 3.2.3 `dependency-audit`
```yaml
---
name: dependency-audit
description: |
  Scans project dependencies for known CVEs, outdated packages, and license
  issues across npm/pip/cargo/go.mod. Use when the user mentions dependency
  vulnerabilities, npm audit, supply chain risk, "is X package safe", weekly
  audit reports, or wants a Renovate-style upgrade plan.
allowed-tools: Bash(npm audit *) Bash(pip-audit *) Bash(osv-scanner *) Read
---
```
- **Input**: `{repo, severity_threshold: low|medium|high|critical, output_format: json|sarif|markdown}`
- **Output**: SARIF 或 `{vulnerabilities, outdated, licenses_flagged}`
- **Idempotency**: cache by lockfile hash + advisory DB date

#### 3.2.4 `security-scan`
```yaml
---
name: security-scan
description: |
  Runs SAST (Semgrep), secrets detection (gitleaks), and IaC scanning
  (Trivy) on a codebase, returns SARIF-formatted findings. Use for security
  review, "find vulnerabilities", pre-merge security gate, or compliance
  reporting (SOC2/PCI/HIPAA mappings).
allowed-tools: Bash(semgrep *) Bash(gitleaks *) Bash(trivy *) Read
---
```
- **Input**: `{repo, scan_types: [sast, secrets, iac, container]}`
- **Output**: SARIF + `{risk_score: 0-100, findings_by_severity}`
- **Idempotency**: read-only，cache by SHA

### 3.3 Trigger Eval Harness（A 級的關鍵 deliverable）

**評分軸明文要求「Skill description 能否被 Claude 精準 trigger」→ 用量化證據打**。

#### 設計
- **跑全 7 個 skill 的 trigger eval**（4 CI/CD + `browser-task` + `sec-extract-10k` + `hello`）。理由：要證明 description 在「**多元 skill 並存**」時還能精準 disambiguate。只測 4 個 CI/CD skill 等於忽視真實情境。
- 每個 skill 一個 `evals/skill-trigger/{skill_name}.json`，含 `should_trigger[]`（≥20 條）+ `should_not_trigger[]`（≥20 條）。`should_not_trigger` 必須包含「會誤觸發到其他 skill」的 query（例如 `dependency-audit` 的 should-not 應含「scan my dependencies for vulnerabilities」這種容易跑去 `security-scan` 的句子）。
- `scripts/run_skill_eval.py`：呼叫 LLM API，每 query 跑 3 次取多數，記錄 trigger / not-trigger
- 60/40 train/test split
- 5 輪 description iteration：先 baseline 全 7 skill；找 TPR/FPR 最差的 skill 改 description；只 re-test 該 skill；重複 5 輪
- 最終 report：每 skill 改前/改後 TPR + FPR 對照表，加上「容易混淆的 skill 配對」分析
- 用 `${TRIGGER_EVAL_MODEL}` 跑 eval（**model 由 user 提供 endpoint，不預設 Haiku**——Haiku trigger 行為可能與 production 用的 Sonnet/Opus 不同，eval 結果不可信）

#### 預估成本
7 skills × 40 query × 3 runs = 840 calls baseline；4 輪 iteration × 120 calls = 480；總 1320 calls × ~$0.002/call (Sonnet 級) ≈ **$3-5 LLM cost** for full 5-round iteration.

#### 範例 `evals/lint-and-test.json`
```json
{
  "skill": "lint-and-test",
  "should_trigger": [
    "run the linter and tests on this PR",
    "does branch feature-x pass CI?",
    "check whether my changes break tests",
    "I want a pre-merge CI gate",
    "...",
    "≥20 變體"
  ],
  "should_not_trigger": [
    "what is linting?",
    "explain CI/CD concepts",
    "set up GitHub Actions for me",
    "...",
    "≥20 變體"
  ]
}
```

### 3.4 Auth 與安全
- **Skill repo as source of truth**：`skills/*/SKILL.md`，manifest scanner 啟動時 parse YAML frontmatter 寫入 Postgres
- **GitHub auth**：fine-grained PAT (10-min TTL via short-lived rotation if time permits；MVP 用 long-lived PAT + env var)
- **API key**：簡單 bearer token，env var 注入
- **Output secret redaction**：所有 skill output 過 regex filter（GitHub token、AWS key、JWT 等）
- **No `Bash(*)` allowed-tools**：所有 skill 明列允許指令

### 3.5 不做的事
- 不做 multi-tenant（schema 預留 `org_id` 欄位）
- 不做 GitHub App（除非時間允許，作為 stretch goal）
- 不做 fine-grained sandbox（worker 隔離靠 Docker container）

---

## 4. Task 3 — SEC 10-K Item-level 抽取（主力）

### 4.1 輸出 Schema

```typescript
type ExtractionResult = {
  filing: {
    cik: string;
    accession: string;
    form_type: "10-K" | "10-K/A" | "10-K405";
    filing_date: string;  // ISO date
    period_ending: string;
    primary_document: string;  // e.g. "aapl-20240928.htm"
    is_inline_xbrl: boolean;
    is_abs_filing: boolean;  // Reg AB schema, special handling
    cover_page_incorporates: {
      target_form: "DEF 14A";
      expected_year: number;
      proxy_120_day_window: [string, string];
      resolved_accession: string | null;  // post-process fill
    } | null;
  };
  items: Item[];
  meta: {
    parser_version: string;
    extraction_time_ms: number;
    cost_usd: number;
    warnings: string[];
  };
};

type Item = {
  part: 1 | 2 | 3 | 4 | 0;  // 0 = synthetic cover-page record
  item_number: string;       // "1", "1A", "1B", "1C", "6", "9C", "cover"
  item_title: string;        // "Business" / "Risk Factors" / etc.
  status: "extracted" | "incorporated_by_reference" | "not_applicable" | "reserved" | "partial" | "non_standard";
  content_text: string;       // cleaned plain text
  char_range_html: [number, number] | null;  // offset in raw HTML
  char_range_text: [number, number] | null;  // offset in cleaned text
  applicable_in_era: boolean;  // false if Item didn't exist in this filing's era
  references?: {                // present when status in {incorporated_by_reference, partial}
    target_form: string;
    expected_year: number;
    resolved_accession: string | null;
  };
  segments?: ItemSegment[];     // present when status = partial
};
```

### 4.2 抽取策略：規則 + LLM 混合

**Phase 1（純規則）**：
1. 取得 filing：先 Submissions API → 找 primary doc → 取 raw HTML/.txt
2. 偵測 era：filing date → 推算哪些 Item 適用（Item 1A 後 2005、1C 後 2023、9C 後 2022）
3. 偵測 ABS：Form type / SIC code → 若是 → `non_standard`，跳過 standard schema
4. 找 cover-page "DOCUMENTS INCORPORATED BY REFERENCE" 區塊
5. **用 edgartools 為 segmentation backbone**：取 `TenK` object，拿到 item-level segments
6. **自加 char-offset 重對齊**：用 `difflib.SequenceMatcher` 把 segment text 重對齊回原始 HTML/text
7. **Status classifier**（規則先試）：
   - `[Reserved]` → `reserved`
   - "Not applicable" / "None" 短回應 → `not_applicable`
   - 強 incorporated-by-reference regex → `incorporated_by_reference`
   - 否則 → `extracted`

**Phase 2（LLM 增援）**：
- Phase 1 不確定的 case（regex 邊緣、heading 怪異、segmentation 失敗）→ LLM call
- LLM model：先用 Haiku 做 status classification（cheap，high recall），不確定的升級到 Sonnet
- LLM call 必有 prompt cache（`prompts/` 紀錄完整版本）

**Phase 3（驗證）**：
- 與 XBRL Company Facts 交叉驗證：抽出的 Item 8 (Financial Statements) 應與 XBRL 數字一致
- Schema validator：每個 Item 的 char_range 不重疊、items 順序符合規範

### 4.3 Eval 設計

#### 4.3.1 Eval set 規模
- **Gold (人工標註) ≥ 3 個**：覆蓋三個時代
  1. Apple 2024 (CIK 320193, 0000320193-24-000123) — canonical post-iXBRL + Item 1C + Part III by-ref
  2. GE 2021 (CIK 40545, 0000040545-21-000011) — cross-ref TOC、conglomerate
  3. Chemical Banking 1995 (CIK 19617, 0000950123-95-000706) — pre-HTML SGML/.txt
- **Silver (用 edgartools 為 baseline，人工抽查) ≥ 7 個**：
  4. Berkshire 2026 (CIK 1067983, 0001193125-26-083899) — conglomerate latest
  5. Berkshire 2019 (CIK 1067983, 0001193125-19-048926) — pre-iXBRL conglomerate
  6. Intel 2022 (CIK 50863, 0000050863-22-000007) — Item 9C 邊界
  7. Intel 2020 (CIK 50863, 0000050863-20-000011) — 大檔 (143 MB)
  8. Apple 2023 (CIK 320193, 0000320193-23-000106) — Item 1C negative control
  9. Goldman 10-K/A 2024 (CIK 886982, 0000886982-24-000012) — Part III only amendment
  10. John Deere Owner Trust 2024 (CIK 1816589, 0001558370-24-000566) — ABS, non_standard
- **Total: 10 個 filings**
- 涵蓋：1995/2019/2020/2021/2022/2023/2024/2026 八個年份；不同產業（科技、銀行、conglomerate、ABS、半導體、汽車金融）

#### 4.3.2 Metrics
| Metric | 定義 |
|---|---|
| Item recall | 應抽出的 item 中正確識別的比例 |
| Item precision | 抽出的 item 中正確的比例 |
| Status accuracy | status 分類正確率（per-class + macro avg） |
| Char-range overlap (IoU) | char_range 與 gold 的 IoU |
| Reference resolution | by-reference item 中正確指向 DEF 14A 的比例 |
| Cost per filing | LLM token cost USD |
| Latency | wall-clock seconds |

#### 4.3.3 沒有 ground truth 怎麼辦
- 人工標註 3 個 → 用為 LLM-as-judge 的 calibration set
- LLM-as-judge 用 Sonnet：input = (filing snippet, predicted item, gold item)，output = `{matches: bool, reason}`
- 報告 LLM-judge 對人工的 agreement rate（need ≥85% 才信）
- XBRL cross-check 為定量驗證（Item 8 數字必與 XBRL fact 一致，否則 fail）

### 4.4 API 設計（deployed）

```
POST /skills/sec-extract-10k/invoke
  body: { cik?: string, accession?: string, url?: string }
  header: Idempotency-Key: <uuid>
  response: 202 { run_id: string }

GET /runs/:run_id
  response: 200 { status: "queued"|"running"|"completed"|"failed", result?: ExtractionResult }

GET /runs/:run_id/stream  (SSE)
  events: progress, log, completed, error
```

### 4.5 成本目標
- 每個 10-K extraction：LLM cost < $0.10（用 Haiku 主導 + Sonnet 邊緣）
- 平均 latency：< 60 秒
- Held-out filings: 預期 cost / latency report 寫進 README

---

## 5. Task 2 — Generalized Browser Automation Agent

### 5.1 架構：Planner-Actor-Validator + Stagehand-style cached selector

```
Natural Language Task
        │
        ▼
┌──────────────┐
│   Planner    │ ──── decompose task into steps + success criteria + negative oracle
│ (Claude Sonnet) ───────┐
└──────────────┘         │
        │                ▼
        ▼          Step plan + oracle
┌──────────────┐
│    Actor     │ ──── execute step
│ (Claude Sonnet) │     - try cached semantic selector first
└──────┬───────┘     - fallback: vision-LLM grounding
        │             - record DOM mutation, URL, screenshot diff
        ▼
┌──────────────┐
│  Validator   │ ──── after each step:
│ (Claude Haiku) ───────  1. DOM/URL/screenshot diff
└──────────────┘         2. negative oracle check
        │                 3. silent-failure guard (cheap signals)
        │                 4. only when anomaly: full LLM trajectory verify
        ▼
   pass / replan / fail-with-reason
```

### 5.2 Self-Maintenance：Selector Cache

- **Key**: `(page_url_template, action_intent)`
- **Value**: `{selector_strategy: "css"|"role"|"text"|"xpath"|"vision", selector: string, last_success: timestamp, dom_hash: string}`
- 命中時直接重放
- 失效偵測：DOM hash diff > threshold → 觸發 re-resolve via LLM
- 持久化到 Postgres `selector_cache` table → **跨 session 證明 self-maintenance**

### 5.3 Self-Correction：多策略 fallback

```
locator ladder (失敗逐層降級):
  1. cached semantic selector
  2. getByRole / a11y tree
  3. data-testid / aria-label
  4. visible text match
  5. CSS class fragment
  6. XPath
  7. vision-LLM (Set-of-Mark on screenshot)

step retry with replan:
  1st failure → same strategy retry once
  2nd failure → escalate to next ladder level
  3rd failure → Validator → Planner replan with reason
  Nth (configurable, default 3 replans) failure → graceful fail with reason
```

### 5.4 Eval Set（30 個任務、6 domain、3 難度）

| Domain | Tasks | 範例 |
|---|---|---|
| 電商搜尋/篩選 | 5 | "在 Etsy 找 < $50 的手工陶瓷馬克杯，回傳前 3 個賣家" |
| 維基/文件查詢 | 5 | "在 Wikipedia 找 'Battle of Hastings' 的死亡人數" |
| 旅遊/地圖 | 5 | "在 Booking 找 5/15-5/17 紐約 4 星以上 < $300/晚" |
| 表單填寫 | 5 | "在 Mailchimp 註冊頁填假資料、回傳成功訊息文字" |
| 多步比價 | 5 | "比較 iPhone 15 在 Best Buy / Apple / Amazon 三家價格" |
| **Finance pack** | 5 | "從 Apple IR 網站抓最新 earnings deck PDF URL"、"用 news 驗證 Apple 10-K Item 1A 中提到的供應鏈風險是否在 2024 已實現" |

每任務必填 fields:
```yaml
task_id: ecom-001
nl_description: "..."
expected_steps: 8
step_cap: 30
token_cap: 50000
wall_clock_cap_s: 180
success_criteria:
  - type: exact_match
    field: returned_price
    value: "$45"
  - type: llm_judge
    prompt: "Did the trajectory locate top 3 sellers under $50?"
negative_oracle:
  must_appear_on_final_page:
    - "$"
    - "Add to Cart"
  must_not_appear:
    - "page not found"
```

### 5.5 評分 pipeline

```
each task:
  1. exact_match success_criteria（cheap, deterministic）
  2. negative_oracle 全部命中
  3. LLM-as-judge（Sonnet, WebVoyager 風格 prompt）
  4. 任三同意 → success
report:
  - per-task: {success, latency, cost, step_count, fail_reason_category}
  - aggregate: success rate by domain + difficulty
  - failure histograms: silent_no_op / hallucinated_success / captcha / timeout / wrong_target
```

### 5.6 反過擬合
- **Base**: 30 個任務 (6 domain × 5)
- **Paraphrase**: base 中 20 個任務各加 2 種 paraphrase → 共 70 task instances
- **Viewport**: base 中 5 個任務跑 mobile + desktop → 額外 5 個 instances
- **Hold-out**: base 30 中保留 10 個**不參與 prompt iteration**（planner/actor/validator prompt 調整時不能用這 10 個的結果）
- **External validation**: WebVoyager 公開子集 10 task（cost ~$2-5）
- 總 evaluation budget：~85-100 task instances + 10 external = ~95-110 跑動

### 5.7 Silent failure 防範組合
1. DOM mutation diff（cheap, 高信心）
2. URL + page title diff
3. Network request 監聽：期待的 mutation 必有 POST/PUT
4. 若前三皆無變化 → 升級 LLM trajectory verifier
5. negative_oracle 比對

### 5.8 部署考量
- Worker image: `mcr.microsoft.com/playwright:v1.49-noble`
- **Local-first**：先在本機 docker-compose 跑通，Day 6 才推上 Zeabur
- Zeabur Dev plan ($5/mo, 4 GB RAM) 開始，**只有 OOM 才升級 Pro ($19/mo)**
- `--shm-size=2gb` Dockerfile 設定
- Volume `/profiles` 存 cookies / browser state
- 並行限制：單 container 最多 2 tab，task queue 排程

---

## 6. Repo 結構

```
interview_hw/
├── README.md                          # 主要 README，整合敘事 + 三題快速 link
├── AI-Coding-Test-{EN,ZH}.md          # 原題目（不刪）
├── CLAUDE.md
├── docs/
│   ├── design/2026-04-30-interview-hw-design.md  # 本檔
│   ├── research/2026-04-30-research-summary.md
│   └── per-task/                      # 各題詳細 README
│       ├── task1-skills-platform.md
│       ├── task2-browser-agent.md
│       └── task3-sec-extractor.md
├── prompts/                           # 必填，主要 prompt 紀錄（評分必看）
│   ├── 00-strategy/
│   ├── task1/
│   ├── task2/
│   └── task3/
├── tasks/
│   ├── todo.md                        # CLAUDE.md 要求的 task tracker
│   └── lessons.md                     # 從 corrections 累積的規則
├── platform/                          # Task 1：Skills 平台
│   ├── gateway/                       # FastAPI
│   ├── workers/
│   │   ├── ci/                        # lint-and-test, build-and-release, etc.
│   │   ├── extractor/                 # Task 3 worker
│   │   └── browser/                   # Task 2 worker
│   ├── inngest/
│   ├── db/migrations/
│   └── docker-compose.yaml            # local dev
├── skills/                            # SKILL.md source of truth
│   ├── lint-and-test/SKILL.md
│   ├── build-and-release/SKILL.md
│   ├── dependency-audit/SKILL.md
│   ├── security-scan/SKILL.md
│   ├── sec-extract-10k/SKILL.md       # Task 3
│   └── browser-task/SKILL.md          # Task 2
├── evals/
│   ├── skill-trigger/                 # Task 1 trigger eval
│   │   ├── lint-and-test.json
│   │   └── ...
│   ├── sec-extraction/                # Task 3 eval
│   │   ├── gold/                      # 人工標註的 3 個 filings
│   │   ├── silver/                    # 7 個 silver filings
│   │   └── runner.py
│   └── browser-tasks/                 # Task 2 eval
│       ├── tasks.yaml
│       └── runner.py
├── scripts/
│   ├── run_skill_eval.py
│   ├── deploy_zeabur.sh
│   └── ...
├── ui/                                # Next.js frontend (UI for invoking skills)
└── zeabur.toml / .zeabur/             # Zeabur deploy config
```

---

## 7. 7-Day Schedule（time-boxed）

> 每天結束前必有 commit + push；無中間斷層的「大爆破式」commit。

| Day | 主軸 | 主要交付 | Hours |
|---|---|---|---|
| **Day 1 (5/1)** | 共通基建 (local) | repo init, gateway + Postgres + Inngest + Redis 跑起來 (local docker-compose only), prompts/ 結構, README skeleton。**Zeabur 不碰** | 10h |
| **Day 2 (5/2)** | Task 3 Phase 1 (local) | edgartools-based segmentation, era detection, status classifier (規則), 跑通 Apple 2024, 把 Apple 標註成第一個 gold | 10-12h |
| **Day 3 (5/3)** | Task 3 Phase 2+3 (local) | Char-offset alignment, LLM 增援, XBRL cross-check, 跑通 GE 2021 + Chemical 1995, 第二/三個 gold | 10-12h |
| **Day 4 (5/4)** | Task 3 完成 + 首次 Zeabur 部署 | 7 個 silver filings, eval runner, status report。**註冊 Zeabur + 第一次部署 Task 3 + extractor service** (Dev plan)。lint-and-test + dependency-audit 兩個 skill 完成 + trigger eval | 10-12h |
| **Day 5 (5/5)** | Task 1 完成 + Task 2 開頭 | build-and-release + security-scan, full skill trigger eval (40 query × 3 runs × 4 skills), deploy 平台到 Zeabur, Task 2 architecture skeleton + Stagehand-style selector cache (local) | 10-12h |
| **Day 6 (5/6)** | Task 2 完成 + 部署 | Planner-Actor-Validator loop, locator ladder, eval runner, 跑通 30 task eval, finance pack 5 task, WebVoyager 10 task external validation。**deploy browser worker to Zeabur Dev plan，OOM 才升 Pro** | 10-12h |
| **Day 7 (5/7) 早上** | 收尾 | Held-out 自我測試（用沒看過的 filing/task）, README finalize, prompts/ 整理, Zeabur 上線檢查, 三個 endpoint smoke test, 提交 | 6-8h |

### 7.1 緩衝邏輯
- 每天結束時 self-check：「held-out 打得進去嗎？」打不進去就 stop & re-plan（CLAUDE.md 規則）
- Day 5 結束若 Task 1 / Task 3 任一未完成 → cut Task 2 finance pack to 3，跳過 WebVoyager external
- Day 6 結束若 Task 2 < 60% pass rate → 把問題寫進 README 「failure modes」section（誠實是 A 級訊號）

### 7.2 不做的（cut list）
- 不做 D 整合題
- 不做 multi-tenant
- 不做 GitHub App auth（用 fine-grained PAT）
- 不做 fine-grained sandbox（worker container 隔離已足）
- 不做自製 sandbox / firecracker
- 不做 auto-scale（Zeabur 手動配置）
- Skill versioning 自加 `version` 欄位但不做 migration policy

---

## 8. 共通工程紀律

### 8.1 Commit history（題目要求「反映真實開發過程」）
- 每完成一個邏輯單元就 commit（不批次）
- Commit message 寫 why，不只是 what
- 不 force-push、不 squash 已 push 的 commit
- 每天至少 5-10 commit（避免「一天一個 mega commit」）

### 8.2 prompts/ 紀錄（題目要求「會實際閱讀」）
- 每個重要的 design / debug / iteration 對話存一份 markdown 摘要
- 結構：date + topic + my_question + claude_response_summary + outcome
- 不要全文倒進去；摘要 + 關鍵 prompt + 關鍵反饋
- 對 trigger eval、failure mode 分析的 prompt 特別保留完整版

### 8.3 README 三層
- **頂層 README**：整合敘事、三題快速 link、deploy URLs、how to run（30 秒 demo）
- **per-task README**：設計決策、API、eval 結果、known failure modes
- **CLAUDE 協作說明**：哪幾段 code 是 AI 主導 vs human 主導，誠實寫

### 8.4 失敗模式誠實揭露（A 級必須）
每題 README 必有 "Known Failure Modes" section：
- Task 3：哪些 filing 類型抽取會失敗、為什麼、如何偵測
- Task 2：哪類網站會 silent-fail、captcha 處理策略
- Task 1：哪些 trigger query 會誤判、description 怎麼改也救不了的邊緣 case

---

## 9. 已決定的 Open Design Questions

研究階段提出的 open questions，spec 階段一次決定（避免實作時反覆）：

### Task 3
1. **`char_range` source**：HTML + text 都記（兩個欄位）
2. **Hybrid items**：`status: partial` + `segments[]` array
3. **ABS 10-K**：`is_abs_filing: true` + `status: non_standard`，不套 standard schema
4. **10-K/A**：獨立 parse，metadata 標 `amends: <accession>`
5. **Cover-page**：synthetic record `part: 0, item_number: "cover"`
6. **Item 6 [Reserved]**：emit `status: reserved` + 空 content（auditability）
7. **Eval gold**：3 人工 + 7 silver = 10 filings
8. **Reference resolution**：MVP 只記錄 `expected_year`，`resolved_accession` post-process（time permitting）

### Task 2
1. **State representation**：DOM-first + vision fallback (Stagehand v3 模式)
2. **Validator model**：Haiku (cheap, called per-step)；Actor / Planner: Sonnet
3. **Selector cache invalidation**：DOM hash + LRU 1000 entry，cross-session 持久化到 Postgres
4. **Captcha**：graceful fail with reason，不串第三方
5. **WebVoyager external**：跑 10 task 子集
6. **任務並行**：單 container 1-2 tab，FastAPI + Inngest queue 管

### Task 1
1. **Skill source**：filesystem (`skills/`) is source of truth；scanner 同步到 Postgres
2. **Auth**：Bearer API key + GitHub fine-grained PAT
3. **Timeout**：所有 skill invoke 一律 async（Inngest queue）
4. **Eval harness**：作為 deliverable，每 skill 40 query × 3 runs

---

## 10. 風險與 Mitigation

| 風險 | 影響 | Mitigation |
|---|---|---|
| Task 3 完美主義 → 吃掉 Task 1/2 時間 | 全盤崩潰 | Day 4 結束 hard cut，剩餘 issue 寫進 known failures |
| Zeabur 首次部署有坑（Day 4 才碰到） | Task 3 部署延誤 | Day 4 早上 8-9 點先做：註冊 + hello world container 部署，預留 2-3h debug。**hello world 不通就立刻投資排查，不能拖** |
| Zeabur browser worker 跑不起來 | Task 2 demo 不能 deploy | Day 6 上線；先在本地 docker 100% 跑通才推。Plan B：Browserbase 託管（最後手段） |
| Inngest self-host 一直壞 | 平台故障，三題都受影響 | Plan B：FastAPI BackgroundTasks + Postgres job table，10 行 code 替代 |
| EDGAR rate limit block | Task 3 eval 跑不完 | Caching + 請求分散；本地保留 raw HTML cache |
| LLM cost 超預算 | 預算崩潰 | 每 worker `cost_cap` env，超過 abort；Haiku 為主、Sonnet 點睛 |
| Eval 時間不夠 | 失去 A 級主訊號 | Skill trigger eval 是 cheapest，Day 4 寫好；其他兩題 eval Day 6 完成；最差只交 Task 3 eval |

---

## 11. 成功指標

**繳交時的 self-check checklist**：

- [ ] 三題各有獨立 Zeabur public URL，每題能用 curl 跑通 happy path
- [ ] Task 3：10 filings eval 結果（recall / precision / status accuracy / cost / latency）寫進 README
- [ ] Task 3：known failure modes 至少 3 個（誠實揭露）
- [ ] Task 2：30 task eval pass rate + WebVoyager 10 task external 數字
- [ ] Task 2：silent failure 偵測示範（一個故意製造的 silent-fail case + 系統如何 catch）
- [ ] Task 1：4 個 skill 各有 trigger eval 報表（before / after description iteration）
- [ ] Task 1：skill repo + manifest 可以 git pull 即時更新（live demo）
- [ ] `prompts/` 有 ≥30 條紀錄（橫跨三題 + strategy）
- [ ] commit history ≥50 commits，跨 7 天
- [ ] README 第一段就清楚講整合敘事
- [ ] 任意一題的 happy-path demo 能在 30 秒內跑出結果

---

## 12. 下一步

1. **本 spec self-review**（fix inline）
2. **User 過 spec**（你看完給我反饋）
3. **進入 writing-plans skill**，依本 spec 拆出可逐 step 執行的 implementation plan
4. **執行**：依 plan 執行，每完成一個 milestone 在 `tasks/todo.md` 打勾、commit

---

## 附錄 A — Pathological Filings 列表（從研究階段保留）

| # | Company | CIK | Accession | 用途 |
|---|---|---|---|---|
| 1 | Chemical Banking | 19617 | 0000950123-95-000706 | Pre-HTML SGML/.txt **(gold)** |
| 2 | Apple 2024 | 320193 | 0000320193-24-000123 | Canonical post-iXBRL + Item 1C **(gold)** |
| 3 | GE 2021 | 40545 | 0000040545-21-000011 | Cross-ref TOC, conglomerate **(gold)** |
| 4 | Berkshire 2026 | 1067983 | 0001193125-26-083899 | Conglomerate, latest |
| 5 | Intel 2022 | 50863 | 0000050863-22-000007 | Item 9C 邊界 |
| 6 | Apple 2023 | 320193 | 0000320193-23-000106 | Item 1C negative control |
| 7 | Goldman 2024 10-K/A | 886982 | 0000886982-24-000012 | Part III only amendment |
| 8 | John Deere Owner Trust 2024 | 1816589 | 0001558370-24-000566 | ABS Reg AB, non_standard |
| 9 | Berkshire 2019 | 1067983 | 0001193125-19-048926 | Pre-iXBRL conglomerate |
| 10 | Intel 2020 | 50863 | 0000050863-20-000011 | 大檔 (143 MB) |

詳細 metadata 與 pathological 原因見 `docs/research/2026-04-30-research-summary.md` Section A5。

## 附錄 B — 本 spec 引用的關鍵 sources

- edgartools v5.30.2: https://github.com/dgunning/edgartools
- Skyvern 2.0 SOTA: https://www.skyvern.com/blog/skyvern-2-0-state-of-the-art-web-navigation-with-85-8-on-webvoyager-eval/
- Stagehand v3: https://www.browserbase.com/blog/stagehand-v3
- Anthropic Skills docs: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- Skill trigger deep dive: https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
- Zeabur Browserless template: https://zeabur.com/templates/ODJQ1Y
- Zeabur Volumes: https://zeabur.com/docs/en-US/data-management/volumes
- Inngest vs Temporal: https://www.inngest.com/compare-to-temporal
- WebVoyager: https://arxiv.org/html/2401.13919v3
- WebArena Verified: https://openreview.net/pdf?id=CSIo4D7xBG
- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- Form 10-K spec: https://www.sec.gov/files/form10-k.pdf
