# Browser-Agent SOTA Delta — 2026-05-01

**Scope**: only what changed since `2026-04-30-research-summary.md` §B (last ~60 days, with focus on 2026-02 → 2026-05).
**Method**: 12 targeted web queries + 6 page fetches. Source list at bottom.
**Audience**: Day 6 implementation of Task 2 (Generalized Browser Automation Agent).

> 對照組（baseline）：design §5 = Planner-Actor-Validator + Postgres `(page_url_template, intent) → selector` cache + 7-tier locator ladder (`getByRole → testid → ARIA → text → CSS → XPath → vision`) + cheap-cascade silent-failure detection + 5 finance-pack tasks。

---

## Executive summary（5 顆子彈，先看這個）

1. **WebVoyager 已飽和**（top 5 在 93-99%），**BrowseComp 才是 2026 的硬 eval**（top model GPT-5.5 Pro = 90.1%, Anthropic 自家 Claude Mythos Preview = 86.9%）。**eval set 必須換主軸**：以 BrowseComp 為 north star，WebVoyager 退為 regression check。
2. **Stagehand v3 (2026-Q2) 已從 Playwright 解耦改用 CDP-native + 自帶 `deepLocator`**（穿 iframe + shadow DOM）+ 自動 action caching（~2× 快、~30% 省 cost）。**我們的 cache 設計仍領先於 simple per-call cache，但 Stagehand 已內建 selector→action 雙層 cache，需 reframe 為 "intent-level + DOM-fingerprint"**。
3. **Anthropic Computer Use beta 已升級至 `computer-use-2025-11-24`**，Opus 4.7 支援 `zoom` action（區域全解析度放大）+ 1:1 coordinate（無需 scale-factor 數學）。**vision-grounding 不再貴得難用，但仍比 DOM-only 慢 2-3×**，作 fallback 即可，不要 promote 為 default。
4. **Hybrid > pure CUA > pure DOM**：Stagehand 官方文件直接列出三模式（DOM / CUA / Hybrid），**Hybrid 是 Browser-Use Cloud (78%) 與 Anthropic-自家 agent 都採用的形態**。我們的 7-tier ladder 應整合為 **「DOM 1-6 → CUA zoom→click」**，不要把 vision 當第 7 層獨立工具。
5. **Playwright 2026 官方反 XPath、反 `nth/first/last`、反 CSS-class**；新推 `ariaSnapshot` 作 page-state diff 的 cheap signal — **silent-failure cascade 應加入 ariaSnapshot diff 作為 DOM diff 的更穩版本**。

---

## §1 Stagehand v3 / Browser-Use / Skyvern delta

### Stagehand v3（**重大更新**，2026-Q1/Q2）
- **CDP-native，移除 Playwright 強依賴**：「Stagehand v3 moved to a CDP-native architecture that talks directly to the browser through the Chrome DevTools Protocol, removing the Playwright dependency and improving performance by 44% on complex DOM interactions.」（NxCode 2026 / Browserbase changelog）
- **Multi-language**：Python / Go / Java / Ruby / Rust 全支援（Browserbase changelog）。對我們：**仍用 Python，但確認 Python SDK 的 feature parity**。
- **三模式正式化**（重要，直接影響我們架構）：
  - `mode: "cua"` — 用 Anthropic / OpenAI / Google CUA 模型，coordinate-based
  - `mode` 預設 — DOM + accessibility tree
  - `mode: "hybrid"` — 兩者並用，Stagehand 推薦 Claude / 最新 Gemini
  > 「Both DOM and CUA modes have their strengths and weaknesses. Hybrid mode combines them.」（docs.stagehand.dev/v3/basics/agent）
- **Action caching 自動內建**：「Stagehand now caches the results of repeated actions, eliminating redundant LLM calls automatically, resulting in up to 2× faster execution and approximately 30% cost reduction on repeat workflows.」（Browserbase changelog）
- **`deepLocator()`**：「creates a special locator that can traverse iframe boundaries and shadow DOM using a simplified syntax. It automatically resolves the correct frame for each operation」（docs.stagehand.dev/v3/references/deeplocator）。**這直接解掉 7-tier ladder 的 shadow DOM 死角**。
- **預設 viewport 1288×711**：偏離者 performance 降低（docs）。

### Browser-Use（2026-Q1/Q2）
- **WebVoyager 89.1%**（GPT-4o）— 仍是最強 OSS baseline。
- **CLI 2.0 released 2026-03-22**：可從 terminal 直接喚起，整合 Claude Code / Cursor。
- **官方 benchmark 結果（browser-use.com/posts/ai-browser-agent-benchmark）**：
  - Browser Use Cloud `bu-ultra` = **78%**（最強）
  - Claude Opus 4.6 = 62.0%
  - Gemini 3.1 Pro = 59.3%
  - Claude Sonnet 4.6 = 59.0%
  - GPT-5 = 52.4%
  - Gemini 2.5 Flash = 35.2%
  > 「Browser Use Cloud leads at 78%, 16 points ahead of the best open-source model.」
  > 「Each bu-ultra step is slower than a smaller LLM, but it completes tasks in far fewer steps, so total wall-clock time is lower.」
- **cost**：100-task run ~ $10 on basic plan，但 **Claude Opus 跑同一組 ~$100/run**。

### Skyvern
- 仍停在 2.0（85.85% on WebVoyager），無重大更新。
- 自家推 **Web-Bench**（更貼近 production，避開 WebVoyager 飽和）。

### SOTA 全景（2026-04, awesomeagents.ai + Steel.dev leaderboard）
| Rank | Agent | WebVoyager | 備註 |
|------|---|---|---|
| 1 | Jina (Om Labs) | 98.9% | 自架 harness，可疑 |
| 2 | Alumnium | 98.6% | A11y tree + visual reasoning |
| 3 | Surfer 2 (H Company) | 97.1% | system-level orchestration |
| 4 | Magnitude | 93.9% | OSS 模組 |
| 5 | AIME Browser-Use | 92.34% | 自製 orchestration |
| 6 | Browser Use | 89.1% | OSS baseline |
| 7 | Operator (OpenAI) | 87.0% | 商業 baseline |

**結論**：WebVoyager 已不再是有效的 SOTA 訊號。任何 >90% 都進不了 top 5 但實作成本天差地別。

---

## §2 Playwright 2026 locator best practices delta

### 確認的官方排序
> 「prioritize user-facing attributes and explicit contracts such as `page.getByRole()`」 — Playwright official best practices

**官方推薦順序**（來源 playwright.dev/docs/locators + best-practices）：
1. `getByRole(name)`
2. `getByLabel`
3. `getByPlaceholder`
4. `getByText`
5. `getByTitle`
6. `getByTestId`
7. CSS / XPath（最後手段）

**注意**：與我們設計的 `getByRole → testid → ARIA → text → CSS → XPath → vision` **順序略有衝突**。Playwright 官方把 testid 排第 6（因為 testid 是 dev-only，破壞 BDD-friendly），但**自動化 agent 場景下 testid 比 text 穩**，我們的順序是正確的。

### 反 pattern（2026 新確認）
1. **不要用 CSS class 選擇器**：「selecting a button by its CSS classes can break when a designer changes something」
2. **不要用 `.first() / .last() / .nth()`**：「when your page changes, Playwright may click on an element you did not intend」
3. **不要用 `waitForTimeout`**：fixed wait → flaky
4. **避免 XPath**：「they're considered a bad practice due to their brittleness and poor performance」
5. **Locator chaining > nth**：「Locators can be chained to narrow down the search to a particular part of the page」

### 新工具：ARIA Snapshot（**2026 推薦，silent-failure 檢測利器**）
> 「With Playwright's Snapshot testing you can assert the accessibility tree of a page against a predefined snapshot template.」（playwright.dev/docs/aria-snapshots）
- `expect(locator).toMatchAriaSnapshot()` 比較 a11y tree（YAML 形式）
- **filter out 純 layout 容器與不可見元素** → 比 raw DOM diff 信號更乾淨
- 對 **partial match** 友善（agent 不需斷言整頁）
- 順序敏感、case-sensitive

### Shadow DOM penetration
- Playwright 官方 locator **預設穿 shadow DOM**，**XPath 例外**（GitHub issue #33547 仍有 slot 邊界問題）
- Stagehand `deepLocator` 是**已驗證可用的封裝**（2026-02-10 release）

### `getByText` regression？
搜尋無新 regression 報告。但 case-sensitivity / whitespace 仍是 standard footgun。

---

## §3 Anthropic Computer Use API 現況（2026-05）

### Beta version
- 最新 header：`computer-use-2025-11-24`
- 支援模型：**Claude Opus 4.7, Opus 4.6, Sonnet 4.6, Opus 4.5**
- 舊 header `computer-use-2025-01-24` 對應 Sonnet 4.5 等（已 deprecate 路徑上）
- ZDR (Zero Data Retention) eligible

### Opus 4.7 重大改善（**對成本/精度都關鍵**）
> 「Claude Opus 4.7 supports up to 2576 pixels on the long edge, and its coordinates are 1:1 with image pixels (no scale-factor conversion required). The 1568-pixel guidance below applies to earlier models.」
- **不再需要寫 scale-factor 程式碼**（少 50 行噁心程式 + 一個 bug 來源）
- **vision 解析度 3.75 MP（trippled vs 4.6）**

### 新 action：`zoom`（**省 token + 升精度**）
- `enable_zoom: true` 啟用
- 可圈選 region `[x1, y1, x2, y2]` 取得該區域**全解析度截圖**
- **抵消 1568px 上限造成的小元素 mis-click**

### Tool tokens（pricing）
- system prompt overhead：466-499 tokens
- computer use tool definition：735 tokens（每次 call）
- 螢幕截圖按 vision pricing 計
- **每步約 1.2-2k tokens**（screenshot + 文字 reasoning），Opus 4.7 = $5/$25 per MTok → 每步 ~$0.01-0.02

### CU 何時值得？
**官方明文限制**（直接引用）：
> 「current computer use latency for human-AI interactions may be too slow ... Focus on use cases where speed isn't critical (for example, background information gathering, automated software testing) in trusted environments.」
> 「Claude may make mistakes or hallucinate when outputting specific coordinates while generating actions.」

**我們的決策**：CU **不該成為 default**，只在 (a) DOM 1-5 都 fail (b) 元素是 canvas / shadow-deep / 非 a11y 元件 時 fallback。

### 替代方案：DOM-only + a11y tree + screenshot context
- 大多數工作流 DOM 已足夠（Stagehand DOM 模式）
- screenshot 只當 LLM context 補足，**不要每步都拍**（每張 ~1k tokens）

---

## §4 Self-healing selector：production 現況

### Healenium（2026 演進）
- 仍是 weighted LCS 為核心：「Healenium uses a modification of the Longest Common Subsequence algorithm with weight that solves the problem of finding the longest subsequence common to all sequences with extra weight for tag, Id, class, value, and other attributes.」
- **新優化**：「The updated algorithm calculates Heuristic Node distance only for nodes which have an index more or equal to the maximum LCS distance, which decreases the number of unnecessary comparisons.」（performance）
- ML-augmented：「enhanced with gradient-boosted priorities identified by machine learning algorithm」
- **核心觸發機制不變**：catch `NoSuchElementException` → LSC → 比較舊新 → 產 healed locators

### 對我們的 implication
- 簡單 `(page_url_template, intent) → selector` cache **+** Healenium-style fallback healer **依然是 industry standard**
- 沒有看到 weighted LCS 被 semantic embedding 完全取代的報告（embedding-based 仍有 hallucination 風險）
- **新增建議**：cache value 不只存 selector，**多存 a11y attributes + fingerprint**，healer 才能跑

### Cache invalidation horror stories（generic web，但 lesson 適用）
- 「Caching is one of those things that seems simple until you're debugging a production issue at 3am, trying to figure out why some users see outdated data and others don't.」
- 主要教訓：**cache key 必須 versioned**，不要只靠 TTL；**hash content 比 hash URL 安全**
- 應用到 selector cache：**cache key 應加 DOM fingerprint hash**，不只是 page_url_template

### Stagehand action caching（**新內建競品**）
- v3 自帶「caches the results of repeated actions」
- 我們的 `(page_url_template, intent) → selector` 仍在 **intent 層**，比 Stagehand action layer 高一層；**敘事可以說「比 Stagehand 的 action cache 多一層 intent-level + Healenium-style healer」**

---

## §5 Silent-failure detection delta

### Cheap-cascade 仍是 consensus
查詢未發現新的更便宜的 detection 方案。**DOM diff → URL diff → network diff → LLM verify** 順序仍然合理。

### 兩個 2026 新訊號
1. **ARIA Snapshot 取代 raw DOM diff**：filter 掉 layout 雜訊，**省 token + 抗 CSS shake**。
   - 推薦：cheap layer 1 改為 `await expect(locator).toMatchAriaSnapshot()`；falsy 才升級到 layer 2-4
2. **「healed test 沉默漂移」是 2026 新討論的 failure mode**：
   > 「Healed tests can silently drift from original intent — the agent adapted to a UI change, but it's now testing a different flow than what you designed, with the assertion passing but asserting the wrong thing.」（bug0.com）
   - **應對**：每個 task 必填 negative oracle（這條原本就在我們設計裡）+ healed selector 必須附「healing diff」log，cron 每天 review

### Reflexion / ProCo 變體
- Reflexion 仍是學界 baseline。**ProCo（Probing-via-Conditioning）沒有新後續**；2026 主流是 Reflexion + episodic memory + LangGraph 框架（HuggingFace Aufklarer 2026 文章）
- **trajectory verifier** 已被內建到 LangGraph / OpenAI Codex subagents（2026-03 GA）的 reflection loop
- **對我們**：**Validator agent prompt 用 Reflexion-style critique（自然語言反思 + 寫進 SQLite）即可，不需要新範式**

---

## §6 Eval methodology delta

### WebVoyager 飽和事實
- top 5 都 >93%
- 「BrowseComp and WebChoreArena are the benchmarks worth tracking in 2026, while WebVoyager provides a useful regression check but shouldn't be the primary eval.」（awesomeagents.ai 2026-04）

### BrowseComp
- 1,266 challenging problems，「measures the ability of AI agents to locate hard-to-find, entangled information on the internet」（OpenAI）
- 主流 leaderboard：
  - GPT-5.5 Pro = 90.1%
  - Claude Mythos Preview = 86.9%
  - Gemini 3.1 Pro = 85.9%
  - Claude Opus 4.6 = 84.0%
  - Deep Research（launch）= 51.5%
- **未飽和**：Deep Research 1 年內從 51.5 → 86.9，但 90% 線已是天花板逼近

### GAIA-Browser
- 466 questions（reasoning + multimodal + browsing + tool use）
- public validation 165 questions
- 仍是 Meta+HF+AutoGPT 維護
- **適合作為「跨能力」regression eval，不適合單純 browser**

### 其他新 bench
- **Skyvern Web-Bench**：商業導向 production-realistic
- **MM-BrowseComp**：multimodal 變體（arxiv 2508.13186）
- **WebChoreArena**：repetitive task domain（少有人碰）

### 對我們的 30-task 設計（**修正建議**）
| 原設計 | 修正 | 理由 |
|---|---|---|
| 6 domain × 3 difficulty × ~2 paraphrase | **+ 5 task BrowseComp slice** + 5 task finance-pack | BrowseComp slice 是「面試官打不穿」的標竿 |
| 評分 = exact match + LLM judge + manual | **+ ariaSnapshot diff 作 cheap auto-eval** | 省 LLM judge cost |
| WebVoyager 子集 10 task 作 external | 改為 **BrowseComp 子集 10 task + WebVoyager 5 task regression** | 對標 SOTA |

---

## §7 Cost-optimal model selection（**最關鍵章節**）

### 2026-Q2 行業 default
- **Stagehand**：model-agnostic（Vercel AI SDK 為底）。文件範例多用 GPT-4.1 或 Claude Sonnet 4.6。
- **Browser-Use Cloud `bu-ultra`**：自家專屬模型（不公開哪個 base），78% 是純 OSS 不可達
- **Browser-Use OSS 推薦**：**Claude Opus 4.6 (62%) > Gemini 3.1 Pro (59.3%) > Sonnet 4.6 (59%) > GPT-5 (52.4%)**

### Cost-per-success 概算（每 100 task）
| Combo | Success | Cost (Browser-Use bench) | $/success |
|---|---|---|---|
| `bu-ultra` (Browser-Use Cloud) | 78% | $10 | $0.13 |
| Claude Opus 4.6 | 62% | ~$100 | $1.61 |
| Gemini 3.1 Pro | 59.3% | TBD | TBD |
| Sonnet 4.6 | 59% | ~$30-40 (估) | ~$0.65 |
| GPT-5 | 52.4% | ~$50 (估) | ~$0.95 |

### NIM ensemble（**use 的 endpoint**）
- DeepSeek V4 Pro: $2.17/MTok blended，**output $3.48 vs Opus 4.7 $25 = 7× 便宜**
- Nemotron 3 Super 120B: 1M context，hybrid MoE，**multi-agent 設計目標**
- Mistral Medium 3.5 128B: mid-tier general

**直接競爭力評估**：
- DeepSeek V4 Pro 在 agentic coding bench 接近 Claude Sonnet 級別（buildfastwithai 2026-04）
- **沒有公開 browser-agent bench 結果**（Browser-Use bench 沒有測 DeepSeek）
- **風險**：browser agent 對 vision 與 coordinate grounding 要求高，DeepSeek/Nemotron 的 vision 能力**未驗證**到 production-grade。Mistral Medium 3.5 同樣未驗證。

### **推薦的三模型 ensemble**（cost-optimal + 利用 NIM + 留 escape hatch）
| 角色 | 模型 | 理由 | Cost |
|---|---|---|---|
| **Planner** | DeepSeek V4 Pro (NIM) | reasoning 強、output 7× 便宜，每任務只 plan 1-2 次 | ~$0.005/plan |
| **Actor (DOM mode)** | Nemotron 3 Super 120B (NIM) | 1M context 適合長 trajectory，多步動作主力 | ~$0.001/step |
| **Actor (vision fallback)** | Claude Sonnet 4.6 / Opus 4.7 | CU beta 唯一 production-grade vision agent | ~$0.02/step |
| **Validator** | Mistral Medium 3.5 (NIM) | binary judge，只需要中等推理 | ~$0.0005/check |
| **Reflexion memory** | DeepSeek V4 Pro 同 planner | 重用 planner 模型即可 | n/a |

**敘事：** 「3 個 NIM 模型擔下 80% 流量，Claude 只在 vision/CUA fallback 才呼叫，整體 cost 比純 Claude 路線降 ~5-7×。」

### Fallback rationale
- 必須**寫 evaluator 對比一組**（DeepSeek planner + Nemotron actor）vs（Sonnet 4.6 全棧）的 BrowseComp slice 10 題 → 用實證決定
- 若 NIM ensemble 在 vision 任務 <40% success → 仍 default DOM-only + Claude CU 限定 fallback

---

## Concrete recommendations for our implementation（**直接搬到 Day 6 plan**）

### 1. 7-tier locator ladder → 修正版
```
1. getByRole(name)           # Playwright 官方第一選擇
2. getByLabel                # 表單元素優先
3. getByTestId               # 自動化專用，比 text 穩
4. getByText                 # 視覺可見的 anchor
5. CSS (no class)            # id / data-* attribute only，禁止 class chain
6. deepLocator (Stagehand)   # 穿 iframe + shadow DOM
7. Computer Use zoom + click # Opus 4.7 fallback (only after 1-6 all fail)
```
**砍掉**：原 ARIA 獨立層（已被 `getByRole` 涵蓋）+ XPath（Playwright 官方反 pattern）。

### 2. Silent-failure cascade → 修正版
```
Layer 1 (cheap):  toMatchAriaSnapshot() diff   # 取代 raw DOM diff
Layer 2 (cheap):  URL + page title diff        # 不變
Layer 3 (cheap):  network response check       # 不變
Layer 4 (med):    healed-selector drift log    # 新增，每次 healer 觸發必記
Layer 5 (exp):    LLM trajectory verifier      # Mistral Medium 3.5 跑，只 layer 1-4 異常時呼叫
Layer 6 (oracle): per-task negative assertion  # 不變
```

### 3. Selector cache schema → 加強版
```sql
-- 原設計只有 (page_url_template, intent) → selector
-- 新版多三欄：
CREATE TABLE selector_cache (
  page_url_template TEXT,
  intent TEXT,
  selector TEXT,
  aria_fingerprint JSONB,    -- 給 Healenium-style healer 用
  dom_hash TEXT,             -- cache invalidation
  last_healed_at TIMESTAMPTZ,
  healing_diff TEXT,         -- 漂移審計
  PRIMARY KEY (page_url_template, intent)
);
```

### 4. Eval set → 修正版
- **30 tasks total**
- 6 domain × 3 difficulty 不變，但 paraphrase 砍為 1 個（保節奏）
- **新增**：BrowseComp 抽樣 5 題 + 我們的 finance-pack 5 題
- 評分主軸：**negative oracle pass rate + ariaSnapshot match + LLM judge tiebreak**
- 不再以 WebVoyager 為主軸，留 5 題作 regression smoke test

### 5. Model ensemble → 寫死 default + 留 toggle
```yaml
agent:
  planner:
    model: deepseek-v4-pro   # via NIM
    fallback: claude-sonnet-4.6
  actor_dom:
    model: nemotron-3-super-120b
    fallback: claude-sonnet-4.6
  actor_vision:
    model: claude-opus-4.7
    beta_header: computer-use-2025-11-24
    enable_zoom: true
  validator:
    model: mistral-medium-3.5-128b
    fallback: claude-haiku-4.5
```

### 6. 新增 Stagehand-style action caching layer
- 不只 cache selector，**cache 整個 (intent, selector, post-action ariaSnapshot)** 三元組
- 跨 session persist 到 Postgres（已有）
- 命中時可**完全跳過 LLM**，直接 replay action

### 7. Computer Use 啟用條件（明文寫入 spec）
```python
# Pseudocode for fallback escalation
if all_dom_locators_failed():
    if element_is_canvas or element_is_shadow_deep_no_a11y:
        # 1. 截全螢幕
        # 2. CU zoom region around suspected location
        # 3. CU click
        invoke_computer_use(zoom=True)
    else:
        raise SelectorNotFound  # 不要無腦升級到 vision
```

---

## Sources

### Stagehand v3
- [Stagehand v3 Changelog](https://www.browserbase.com/changelog/stagehand-v3) — Browserbase, 2026
- [Stagehand Docs / Agent](https://docs.stagehand.dev/v3/basics/agent) — accessed 2026-05-01
- [Stagehand deepLocator](https://docs.stagehand.dev/v3/references/deeplocator) — accessed 2026-05-01
- [Stagehand vs Browser Use vs Playwright (NxCode)](https://www.nxcode.io/resources/news/stagehand-vs-browser-use-vs-playwright-ai-browser-automation-2026) — 2026
- [Stagehand shadow DOM release](https://www.browserbase.com/changelog/stagehand-new-release-feat-shadow-dom-support) — 2026-02-10

### Browser-Use & Skyvern
- [Browser Use benchmark post](https://browser-use.com/posts/ai-browser-agent-benchmark) — bu-ultra 78%
- [Skyvern 2.0 launch](https://www.skyvern.com/blog/skyvern-2-0-state-of-the-art-web-navigation-with-85-8-on-webvoyager-eval/)
- [Skyvern Web-Bench](https://blog.skyvern.com/web-bench-a-new-way-to-compare-ai-browser-agents/)
- [Browser Use vs Stagehand (Skyvern blog)](https://www.skyvern.com/blog/browser-use-vs-stagehand-which-is-better/) — 2026-02

### Leaderboards
- [Steel.dev AI Browser Agent Leaderboard](https://leaderboard.steel.dev/) — 2026-04
- [Awesome Agents Web Agent Benchmarks](https://awesomeagents.ai/leaderboards/web-agent-benchmarks-leaderboard/) — 2026-04
- [BrowseComp Leaderboard (LLM-Stats)](https://llm-stats.com/benchmarks/browsecomp)
- [BenchLM WebVoyager](https://benchlm.ai/benchmarks/webVoyager)

### Playwright
- [Playwright Locators](https://playwright.dev/docs/locators) — official 2026
- [Playwright Best Practices](https://playwright.dev/docs/best-practices)
- [Playwright ARIA Snapshots](https://playwright.dev/docs/aria-snapshots) — 2026 stable
- [Anti-Patterns in Playwright (Medium)](https://medium.com/@gunashekarr11/anti-patterns-in-playwright-people-dont-realize-they-re-doing-00f84cd7dff0)
- [GitHub Issue #33547 — slot in shadow DOM](https://github.com/microsoft/playwright/issues/33547)

### Anthropic Computer Use
- [Computer Use Tool Docs (2026-05)](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool) — beta header `computer-use-2025-11-24`
- [Anthropic Pricing 2026](https://platform.claude.com/docs/en/about-claude/pricing)
- [Claude Opus 4.7 Release](https://www.anthropic.com/news/claude-opus-4-7)
- [Computer Use 2026 Guide (LaoZhang)](https://blog.laozhang.ai/en/posts/claude-computer-use)

### Self-healing
- [Healenium](https://healenium.io/) — weighted LCS + ML
- [Healenium Docs](https://healenium.io/docs/how_healenium_works)
- [Healenium Medium overview](https://medium.com/geekculture/healenium-self-healing-library-for-selenium-test-automation-26c2358629c5)

### Eval & Methodology
- [BrowseComp (OpenAI)](https://openai.com/index/browsecomp/)
- [GAIA Leaderboard (HAL Princeton)](https://hal.cs.princeton.edu/gaia)
- [BrowseComp-Plus (ACL 2026)](https://github.com/texttron/BrowseComp-Plus)
- [MM-BrowseComp (arxiv 2508.13186)](https://arxiv.org/html/2508.13186v1)
- [Bug0 — agent QA failure modes 2026](https://bug0.com/blog/ai-testing-browser-agent-tools-wont-fix-qa-2026)

### Models
- [DeepSeek V4 Pro Pricing 2026 (DeepInfra)](https://deepinfra.com/blog/deepseek-v4-pro-pricing-guide-2026-providers-cost-analysis)
- [Artificial Analysis — DeepSeek V4 Pro](https://artificialanalysis.ai/models/deepseek-v4-pro)
- [DeepSeek V4 Pro Review (BuildFastWithAI)](https://www.buildfastwithai.com/blogs/deepseek-v4-pro-review-2026)
- [Reflexion paper (arxiv 2303.11366)](https://arxiv.org/pdf/2303.11366)
- [HuggingFace 2026 Reflective Agent trends](https://huggingface.co/blog/aufklarer/ai-trends-2026-test-time-reasoning-reflective-agen)
