// browser-agent demo — vanilla JS, no framework.
//
// Flow: POST /api/run → poll /api/status/{id} every 1.5s → on done,
// GET /api/result/{id} for the full TaskResult and render.
//
// The trajectory table is the centerpiece — every column is a signal a
// reviewer should be able to read at a glance: tier, cache_hit, healed,
// validator decision, silent-failure signals, replan boundaries.

const POLL_INTERVAL_MS = 1500;

// ── Examples ──────────────────────────────────────────────────────────────
// Crafted to each demonstrate a different agent capability. The edge_case
// task should trigger a 404 → REPLAN → recovery, which is the most
// reviewer-relevant behavior we have.
const EXAMPLES = {
  wiki: {
    task: "go to en.wikipedia.org and find the casualty count from the Battle of Hastings",
    starting_url: "https://en.wikipedia.org/wiki/Battle_of_Hastings",
    max_steps: 15,
    max_seconds: 120,
  },
  sec: {
    task: "find the accession number of Apple Inc's most recent 10-K filing on SEC EDGAR",
    starting_url: "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193&type=10-K&dateb=&owner=include&count=40",
    max_steps: 12,
    max_seconds: 150,
  },
  edge: {
    task: "go to https://en.wikipedia.org/wiki/This_Page_Definitely_Does_Not_Exist_2026 and find information about cats; if the page does not exist, navigate to a real cats article instead",
    starting_url: "https://en.wikipedia.org/wiki/This_Page_Definitely_Does_Not_Exist_2026",
    max_steps: 15,
    max_seconds: 150,
  },
};

// ── Element refs ──────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);

const els = {
  taskInput: $("task-input"),
  startingUrl: $("starting-url"),
  maxSteps: $("max-steps"),
  maxSeconds: $("max-seconds"),
  runBtn: $("run-btn"),
  runMsg: $("run-msg"),
  statusBadge: $("status-badge"),
  evalBanner: $("eval-banner"),

  planCard: $("plan-card"),
  planToggle: $("plan-toggle"),
  planList: $("plan-list"),
  planSummary: $("plan-summary"),

  trajectoryCard: $("trajectory-card"),
  trajectoryBody: $("trajectory-body"),
  trajectorySummary: $("trajectory-summary"),

  resultCard: $("result-card"),
  resultStatus: $("result-status"),
  resultStats: $("result-stats"),
  resultExtracted: $("result-extracted"),
  resultFail: $("result-fail"),
  failBlock: $("fail-block"),
};

// ── Status badge + elapsed-time ticker ────────────────────────────────────
let elapsedTimer = null;
let elapsedStart = 0;
// Track how many trajectory rows we've already painted so streaming polls can
// append-only instead of rebuilding the whole table each cycle.
let lastRenderedTrajectoryLength = 0;

function setStatus(state, text) {
  els.statusBadge.className = `badge badge-${state}`;
  els.statusBadge.textContent = text || state;
  if (state === "running") {
    elapsedStart = Date.now();
    if (elapsedTimer) clearInterval(elapsedTimer);
    elapsedTimer = setInterval(updateRunningMessage, 1000);
    updateRunningMessage();
  } else {
    if (elapsedTimer) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
    // Reset so any in-flight `updateRunningMessage` (e.g. from a stale
    // pollLoop tick that lands after we transition to done/error) early-exits
    // via its `!elapsedStart` guard rather than overwriting the final message.
    elapsedStart = 0;
  }
}

function updateRunningMessage() {
  if (!activeTaskId || !elapsedStart) return;
  const seconds = Math.floor((Date.now() - elapsedStart) / 1000);
  const stepCount = els.trajectoryBody.children.length;
  const stepText = stepCount > 0
    ? `${stepCount} step${stepCount === 1 ? "" : "s"} so far`
    : "planner working…";
  els.runMsg.textContent = `running · ${seconds}s elapsed · ${stepText}`;
  els.runMsg.className = "run-msg";
}

// ── Eval banner ───────────────────────────────────────────────────────────
async function loadEvalBanner() {
  try {
    const r = await fetch("/api/eval-summary");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    if (!data.available) {
      els.evalBanner.textContent = "eval: n/a";
      return;
    }
    const total = data.n || 0;
    const ok = data.n_ok || 0;
    els.evalBanner.textContent = `eval: ${ok}/${total} held-out tasks`;
    if (ok === total && total > 0) {
      els.evalBanner.classList.add("eval-good");
    }
  } catch (e) {
    els.evalBanner.textContent = "eval: n/a";
  }
}

// ── Examples ──────────────────────────────────────────────────────────────
function fillExample(key) {
  const ex = EXAMPLES[key];
  if (!ex) return;
  els.taskInput.value = ex.task;
  els.startingUrl.value = ex.starting_url || "";
  els.maxSteps.value = ex.max_steps;
  els.maxSeconds.value = ex.max_seconds;
}
document.querySelectorAll(".example").forEach((btn) => {
  btn.addEventListener("click", () => fillExample(btn.dataset.example));
});

// ── Plan toggle ───────────────────────────────────────────────────────────
els.planToggle.addEventListener("click", () => {
  els.planCard.classList.toggle("collapsed");
});

// ── Run task ──────────────────────────────────────────────────────────────
let activeTaskId = null;

els.runBtn.addEventListener("click", async () => {
  const task = els.taskInput.value.trim();
  if (!task) {
    els.runMsg.textContent = "task description required";
    els.runMsg.className = "run-msg error";
    return;
  }

  // Reset prior render before kicking off a new task.
  resetPanels();

  const payload = {
    task,
    starting_url: els.startingUrl.value.trim() || null,
    max_steps: parseInt(els.maxSteps.value, 10) || 25,
    max_seconds: parseInt(els.maxSeconds.value, 10) || 180,
  };

  els.runBtn.disabled = true;
  els.runMsg.textContent = "submitting…";
  els.runMsg.className = "run-msg";

  try {
    const r = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const detail = (await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`;
      throw new Error(detail);
    }
    const data = await r.json();
    activeTaskId = data.task_id;
    setStatus("running", "running");
    pollLoop(activeTaskId);
  } catch (e) {
    els.runMsg.textContent = `error: ${e.message}`;
    els.runMsg.className = "run-msg error";
    setStatus("error", "error");
    els.runBtn.disabled = false;
  }
});

function resetPanels() {
  els.planCard.hidden = true;
  els.planList.innerHTML = "";
  els.planSummary.textContent = "";
  els.trajectoryCard.hidden = true;
  els.trajectoryBody.innerHTML = "";
  els.trajectorySummary.textContent = "";
  lastRenderedTrajectoryLength = 0;
  els.resultCard.hidden = true;
  els.resultStats.innerHTML = "";
  els.resultExtracted.textContent = "—";
  els.failBlock.hidden = true;
}

async function pollLoop(taskId) {
  while (true) {
    await sleep(POLL_INTERVAL_MS);
    if (taskId !== activeTaskId) return; // user kicked off a new task

    let status;
    try {
      const r = await fetch(`/api/status/${taskId}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      status = await r.json();
    } catch (e) {
      els.runMsg.textContent = `polling error: ${e.message}`;
      els.runMsg.className = "run-msg error";
      setStatus("error", "error");
      els.runBtn.disabled = false;
      return;
    }

    // Stream trajectory: backend appends to trajectory_so_far via
    // handlers.run_task event_callback as each step finishes. renderTrajectory
    // is incremental — it only paints rows past lastRenderedTrajectoryLength.
    if (status.trajectory_so_far && status.trajectory_so_far.length > 0) {
      renderTrajectory(status.trajectory_so_far);
    }

    if (status.status === "done") {
      // Pull the full result for the richest possible render.
      try {
        const r = await fetch(`/api/result/${taskId}`);
        const result = await r.json();
        renderResult(result);
        setStatus(result.ok ? "done" : "error", result.ok ? "done" : "failed");
      } catch (e) {
        els.runMsg.textContent = `result fetch error: ${e.message}`;
        els.runMsg.className = "run-msg error";
        setStatus("error", "error");
      }
      els.runBtn.disabled = false;
      return;
    }
    if (status.status === "error") {
      els.runMsg.textContent = `task failed: ${status.error || "unknown"}`;
      els.runMsg.className = "run-msg error";
      setStatus("error", "error");
      els.runBtn.disabled = false;
      return;
    }
    // pending or running → keep polling
  }
}

function sleep(ms) {
  return new Promise((res) => setTimeout(res, ms));
}

// ── Render: full TaskResult ───────────────────────────────────────────────
function renderResult(result) {
  // 1. Plan panel — derived from the trajectory's `step` fields. We don't
  //    have a separate "initial plan" payload (replans rewrite `steps` in
  //    place), so we just show the steps that actually executed.
  const steps = result.trajectory.map((e) => e.step);
  els.planList.innerHTML = "";
  for (const s of steps) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="action-tag">${escapeHTML(s.action_type)}</span>${escapeHTML(s.target_intent || "")}`;
    els.planList.appendChild(li);
  }
  els.planSummary.textContent = `${steps.length} step${steps.length === 1 ? "" : "s"}`;
  els.planCard.hidden = false;
  els.planCard.classList.add("collapsed"); // collapsed by default

  // 2. Trajectory
  renderTrajectory(result.trajectory);

  // 3. Result stats + extracted
  const stats = [
    ["status", result.ok ? "OK" : "FAIL"],
    ["fail_reason", result.fail_reason || "—"],
    ["duration", `${(result.duration_ms / 1000).toFixed(1)}s`],
    ["steps", String(result.trajectory.length)],
    ["cache_hits", String(result.selector_cache_hits || 0)],
    ["cache_writes", String(result.selector_cache_writes || 0)],
    ["heals", String(result.healed_selector_count || 0)],
  ];
  els.resultStats.innerHTML = stats
    .map(
      ([k, v]) =>
        `<div class="stat-tile"><div class="stat-label">${escapeHTML(k)}</div><div class="stat-value">${escapeHTML(v)}</div></div>`,
    )
    .join("");

  if (result.extracted_content !== null && result.extracted_content !== undefined) {
    els.resultExtracted.textContent = JSON.stringify(result.extracted_content, null, 2);
  } else {
    els.resultExtracted.textContent = "(no extracted content)";
  }

  els.resultStatus.className = `badge badge-${result.ok ? "done" : "error"}`;
  els.resultStatus.textContent = result.ok ? "ok" : "failed";

  if (!result.ok) {
    els.failBlock.hidden = false;
    els.resultFail.textContent = result.fail_reason || "unknown";
  } else {
    els.failBlock.hidden = true;
  }

  els.resultCard.hidden = false;
}

// ── Render: trajectory rows ───────────────────────────────────────────────
// Trajectory is append-only on the server (handlers.run_task only appends to
// `trajectory` — replans don't rewrite history), so the UI can append-only too.
// We track lastRenderedTrajectoryLength to avoid the O(N²) thrash of clearing
// and rebuilding the whole table every poll.
function renderTrajectory(events) {
  els.trajectoryCard.hidden = false;

  // Defensive: if the list shrank (e.g. a new task started before resetPanels
  // ran), drop everything and rebuild.
  if (events.length < lastRenderedTrajectoryLength) {
    els.trajectoryBody.innerHTML = "";
    lastRenderedTrajectoryLength = 0;
  }
  for (let i = lastRenderedTrajectoryLength; i < events.length; i++) {
    els.trajectoryBody.appendChild(buildTrajectoryRow(events[i], i));
  }
  lastRenderedTrajectoryLength = events.length;

  let replanCount = 0;
  let abortCount = 0;
  for (const event of events) {
    const decision = (event.validation && event.validation.decision) || "pass";
    if (decision === "replan") replanCount += 1;
    if (decision === "abort") abortCount += 1;
  }
  els.trajectorySummary.textContent = [
    `${events.length} step${events.length === 1 ? "" : "s"}`,
    replanCount ? `${replanCount} replan${replanCount === 1 ? "" : "s"}` : null,
    abortCount ? `${abortCount} abort${abortCount === 1 ? "" : "s"}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function buildTrajectoryRow(event, i) {
  const { step, result, validation } = event;
  const tr = document.createElement("tr");

  const decision = (validation && validation.decision) || "pass";
  if (decision === "replan") tr.classList.add("row-replan");
  if (decision === "abort") tr.classList.add("row-abort");

  const tdNum = td(String(i + 1));
  const tdAction = td(escapeHTML(step.action_type));

  let intentText = step.target_intent || "";
  if (step.action_type === "navigate" && step.url) {
    intentText = step.url;
  } else if (step.action_type === "type" && step.value) {
    intentText = `${intentText} ← ${step.value}`;
  } else if (step.action_type === "extract" && step.extract_query) {
    intentText = `${intentText} ← q: ${step.extract_query}`;
  }
  const tdIntent = td(escapeHTML(intentText));
  tdIntent.className = "col-intent";

  const tier = result.locator_tier || "—";
  const tierClass = (tier === "cached" && result.cache_hit) ? "tier tier-cached" : "tier";
  const tdTier = document.createElement("td");
  tdTier.innerHTML = `<span class="${tierClass}">${escapeHTML(tier)}</span>`;

  const tdSelector = td(result.selector ? escapeHTML(result.selector) : "—");
  tdSelector.className = "col-selector";

  // tier=cached + cache_hit=false ⇒ resolver fell into the heal branch (the
  // stored selector still resolved, just had to re-fingerprint and rewrite).
  const tdFlags = document.createElement("td");
  const chips = [];
  if (result.cache_hit) chips.push(`<span class="flag-chip flag-good">cache_hit</span>`);
  if (result.locator_tier === "cached" && !result.cache_hit) {
    chips.push(`<span class="flag-chip flag-warn">healed</span>`);
  }
  if (!result.success) chips.push(`<span class="flag-chip flag-bad">step_fail</span>`);
  tdFlags.innerHTML = chips.join(" ") || "—";

  const tdValidator = document.createElement("td");
  const conf = validation && typeof validation.confidence === "number"
    ? ` (${validation.confidence.toFixed(2)})` : "";
  tdValidator.innerHTML = `<span class="validator-${decision}">${escapeHTML(decision.toUpperCase())}</span>${conf}`;
  tdValidator.title = (validation && validation.reason) || "";

  const tdSignals = document.createElement("td");
  const signals = (validation && validation.silent_failure_signals) || [];
  if (signals.length === 0) {
    tdSignals.textContent = "—";
  } else {
    tdSignals.innerHTML = `<div class="signals">${signals
      .map((s) => `<span class="flag-chip ${signalClass(s)}">${escapeHTML(s)}</span>`)
      .join("")}</div>`;
  }

  tr.append(tdNum, tdAction, tdIntent, tdTier, tdSelector, tdFlags, tdValidator, tdSignals);
  return tr;
}

// "good" signal: url changed after navigate → as expected.
// "bad" signal: no_visible_state_change_after_mutating_action → silent fail.
function signalClass(signal) {
  const s = signal.toLowerCase();
  if (s.includes("no_visible") || s.includes("violat") || s.includes("missing")) return "flag-bad";
  if (s.includes("url_changed") || s.includes("dom_changed")) return "flag-good";
  return "flag-warn";
}

// ── Helpers ───────────────────────────────────────────────────────────────
function td(text) {
  const el = document.createElement("td");
  el.textContent = text;
  return el;
}
function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

// ── Boot ──────────────────────────────────────────────────────────────────
loadEvalBanner();
