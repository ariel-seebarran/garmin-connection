// ---- State ----
const state = {
  messages: [],
  streaming: false,
  abortController: null,
};

// ---- Chart state ----
let _chartInstance = null;
let _currentMetric = null;
let _currentDays = 30;

// ---- Init ----
document.addEventListener("DOMContentLoaded", () => {
  loadStats();
  loadActivities();
  initDataSource();
  document.addEventListener("keydown", e => { if (e.key === "Escape") { closeChart(); closePerfModal(); closeActivityModal(); closeGarminModal(); closeStravaModal(); } });
  initSidebarResize();
  initCardDrag();
  handleStravaCallback();
});

// ---- Stats ----
async function loadStats() {
  try {
    const res = await fetch("/api/stats");
    if (!res.ok) return;
    const d = await res.json();

    setText("weeklyDist", d.weekly_distance ?? "—");
    setText("totalDist", d.total_distance_30d ?? "—");
    setText("sleepScore", d.avg_sleep_score_7d ?? "—");
    setText("hrv", d.avg_hrv_7d ?? "—");
    setText("rhr", d.avg_resting_hr_7d ?? "—");
    setText("lastPace", d.last_run_pace ?? "—");
    setText("lastRunDate", d.last_run_date ? `${d.last_run_date} · ${d.last_run_distance}km` : "No runs yet");

    // Training readiness — color-coded by level
    const trEl = document.getElementById("trainingReadiness");
    const trLevelEl = document.getElementById("trainingReadinessLevel");
    if (trEl && d.training_readiness != null) {
      trEl.textContent = d.training_readiness;
      const level = (d.training_readiness_level || "").toUpperCase();
      trEl.style.color = (level === "OPTIMAL" || level === "GOOD")
        ? "var(--green)"
        : (level === "MODERATE") ? "var(--yellow)"
        : (level === "LOW" || level === "POOR") ? "var(--red)"
        : "var(--accent)";
      if (trLevelEl) trLevelEl.textContent = level ? level.toLowerCase() : "/100";
    }

    setText("vo2Max", d.vo2_max != null ? d.vo2_max.toFixed(1) : "—");

    if (d.steps_today != null) {
      setText("stepsToday", d.steps_today.toLocaleString());
      // If falling back to a prior day, show date in sub-label
      const stepsSub = document.querySelector('#stepsToday ~ .stat-sub, .stat-card[data-card="steps"] .stat-sub');
      if (stepsSub) stepsSub.textContent = d.steps_date ? d.steps_date : "steps";
    }

    const intensityEl = document.getElementById("intensityMins");
    if (intensityEl && d.intensity_minutes_week != null) {
      intensityEl.textContent = d.intensity_minutes_week;
      intensityEl.style.color = d.intensity_minutes_week >= 150
        ? "var(--green)"
        : d.intensity_minutes_week >= 75 ? "var(--yellow)"
        : "var(--accent)";
    }

    const syncEl = document.getElementById("lastSync");
    if (d.last_sync) {
      syncEl.textContent = `Last sync: ${d.last_sync}`;
    }
    if (d.total_indexed) {
      syncEl.textContent += ` · ${d.total_indexed} runs indexed`;
    }

    const hasGarminData = d.avg_sleep_score_7d != null || d.avg_hrv_7d != null || d.avg_resting_hr_7d != null;
    updateSidebarForSource(hasGarminData);
  } catch (e) {
    console.warn("Stats load failed:", e);
  }
}

// ---- Data source detection ----
async function initDataSource() {
  try {
    const res = await fetch("/api/strava/status");
    const d = await res.json();
    window._stravaConnected = d.connected;
    window._stravaAthlete = d.athlete_name || null;
    updateStravaBtn();
  } catch (_) {
    window._stravaConnected = false;
  }
}

function updateSidebarForSource(hasGarminData) {
  const stravaOnly = window._stravaConnected && !hasGarminData;
  document.body.classList.toggle("strava-only", stravaOnly);
}

// ---- Activities ----
async function loadActivities() {
  try {
    const res = await fetch("/api/activities?limit=30");
    if (!res.ok) return;
    const { activities } = await res.json();
    renderActivities(activities);
  } catch (e) {
    console.warn("Activities load failed:", e);
  }
}

function renderActivities(activities) {
  const list = document.getElementById("activityList");
  if (!activities.length) {
    list.innerHTML = '<div class="empty-state">Sync your Garmin data to see activities</div>';
    return;
  }
  list.innerHTML = activities.map(a => `
    <div class="activity-item" onclick="openActivityModal('${a.id}')">
      <div class="activity-type-badge">${fmt_type(a.type)}</div>
      <div class="activity-row1">
        <span class="activity-date">${a.date}</span>
        <span class="activity-dist">${a.distance_km}km</span>
      </div>
      <div class="activity-row2">
        ${a.pace ? `<span class="activity-meta">⏱ ${a.pace}</span>` : ""}
        ${a.avg_hr ? `<span class="activity-meta">♥ ${a.avg_hr}bpm</span>` : ""}
        ${a.duration ? `<span class="activity-meta">⌚ ${a.duration}</span>` : ""}
        ${a.elevation_gain ? `<span class="activity-meta">↑ ${Math.round(a.elevation_gain)}m</span>` : ""}
        ${a.power ? `<span class="activity-meta">⚡ ${a.power}W</span>` : ""}
        ${a.tss ? `<span class="activity-meta">TSS ${a.tss}</span>` : ""}
      </div>
    </div>
  `).join("");
}

function fmt_type(type) {
  const map = {
    running: "Run", trail_running: "Trail", treadmill_running: "Treadmill",
    track_running: "Track", ultra_run: "Ultra", virtual_run: "Virtual",
  };
  return map[type] || type || "Run";
}

function askAboutActivity(date, dist) {
  const input = document.getElementById("messageInput");
  input.value = `Analyze my ${dist} run on ${date}. What does it tell you about my current fitness?`;
  input.focus();
  autoResize(input);
}

// ---- Chat ----

function newChat() {
  if (state.streaming) stopGeneration();
  state.messages = [];
  const container = document.getElementById("chatMessages");
  container.innerHTML = "";
  container.appendChild(document.getElementById("welcomeTpl").content.cloneNode(true));
}

async function sendMessage() {
  const input = document.getElementById("messageInput");
  const text = input.value.trim();
  if (!text || state.streaming) return;

  input.value = "";
  autoResize(input);

  state.messages.push({ role: "user", content: text });
  document.querySelector(".welcome-message")?.remove();
  document.querySelectorAll(".regenerate-container").forEach(el => el.remove());

  appendMessage("user", text);
  await runChat();
}

async function runChat() {
  const goal = null;
  const assistantEl = appendMessage("assistant", "");
  const contentEl = assistantEl.querySelector(".message-content");
  contentEl.classList.add("streaming-cursor");

  setStreaming(true);

  let fullText = "";
  let currentToolEl = null;

  try {
    state.abortController = new AbortController();
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: state.messages, goal }),
      signal: state.abortController.signal,
    });

    if (!res.ok) {
      const err = await res.json();
      contentEl.textContent = `Error: ${err.detail}`;
      contentEl.classList.remove("streaming-cursor");
      setStreaming(false);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let streamDone = false;

    while (!streamDone) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        try {
          const evt = JSON.parse(line.slice(6));
          if (evt.type === "tool_start") {
            currentToolEl = appendToolIndicator(assistantEl, evt.label);
          } else if (evt.type === "tool_end") {
            if (currentToolEl) { currentToolEl.remove(); currentToolEl = null; }
          } else if (evt.type === "text") {
            fullText += evt.content;
            contentEl.innerHTML = renderMarkdown(fullText);
          } else if (evt.type === "error") {
            contentEl.textContent = `Error: ${evt.message}`;
            contentEl.classList.remove("streaming-cursor");
          } else if (evt.type === "done") {
            streamDone = true;
            break;
          }
        } catch (_) {}
      }
    }
  } catch (e) {
    if (e.name === "AbortError") {
      if (!fullText) assistantEl.remove();
    } else {
      contentEl.textContent = `Connection error: ${e.message}`;
    }
  }

  assistantEl.querySelectorAll(".tool-indicator").forEach(el => el.remove());
  contentEl.classList.remove("streaming-cursor");

  if (fullText) {
    state.messages.push({ role: "assistant", content: fullText });
    showRegenerateButton();
  }

  setStreaming(false);
  scrollToBottom();
}

function setStreaming(on) {
  state.streaming = on;
  document.getElementById("sendBtn").style.display = on ? "none" : "flex";
  document.getElementById("stopBtn").style.display = on ? "flex" : "none";
  if (!on) state.abortController = null;
}

function stopGeneration() {
  if (state.abortController) state.abortController.abort();
}

async function regenerate() {
  if (state.streaming || state.messages.length < 2) return;

  document.querySelectorAll(".regenerate-container").forEach(el => el.remove());

  // Remove last assistant message from DOM and state
  const allMsgEls = [...document.querySelectorAll("#chatMessages .message")];
  allMsgEls[allMsgEls.length - 1]?.remove();
  if (state.messages.at(-1)?.role === "assistant") state.messages.pop();

  await runChat();
}

function showRegenerateButton() {
  document.querySelectorAll(".regenerate-container").forEach(el => el.remove());
  const div = document.createElement("div");
  div.className = "regenerate-container";
  div.innerHTML = `<button class="regenerate-btn" onclick="regenerate()">↺ Regenerate response</button>`;
  document.getElementById("chatMessages").appendChild(div);
}

function editMessage(msgEl) {
  if (state.streaming) return;
  const allMsgEls = [...document.querySelectorAll("#chatMessages .message")];
  const idx = allMsgEls.indexOf(msgEl);
  if (idx < 0 || idx >= state.messages.length) return;

  const content = state.messages[idx].content;
  state.messages = state.messages.slice(0, idx);
  allMsgEls.slice(idx).forEach(el => el.remove());
  document.querySelectorAll(".regenerate-container").forEach(el => el.remove());

  const input = document.getElementById("messageInput");
  input.value = content;
  autoResize(input);
  input.focus();
}

function appendMessage(role, content) {
  const container = document.getElementById("chatMessages");
  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.innerHTML = `
    <div class="message-avatar">${role === "user" ? "👤" : "🤖"}</div>
    <div class="message-body">
      <div class="message-role">
        ${role === "user" ? "You" : "Coach Claude"}
        ${role === "user" ? '<button class="edit-btn" onclick="editMessage(this.closest(\'.message\'))" title="Edit message">✏</button>' : ""}
      </div>
      <div class="message-content">${role === "user" ? escapeHtml(content) : renderMarkdown(content)}</div>
    </div>
  `;
  container.appendChild(div);
  scrollToBottom();
  return div;
}

function appendToolIndicator(messageEl, label) {
  const body = messageEl.querySelector(".message-body");
  const div = document.createElement("div");
  div.className = "tool-indicator";
  div.innerHTML = `<div class="tool-spinner"></div><span>${escapeHtml(label)}</span>`;
  body.insertBefore(div, body.querySelector(".message-content"));
  scrollToBottom();
  return div;
}

function quickPrompt(text) {
  const input = document.getElementById("messageInput");
  input.value = text;
  autoResize(input);
  input.focus();
}

function handleKey(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

function autoResize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 160) + "px";
}

function scrollToBottom() {
  const el = document.getElementById("chatMessages");
  el.scrollTop = el.scrollHeight;
}

// ---- Garmin Modal ----
function openGarminModal() {
  document.getElementById("syncModal").classList.add("open");
  document.getElementById("syncStatus").textContent = "";
  document.getElementById("syncStatus").className = "sync-status";
  document.getElementById("mfaGroup").style.display = "none";
  document.getElementById("mfaCode").value = "";
}

function closeGarminModal(e) {
  if (!e || e.target === document.getElementById("syncModal")) {
    document.getElementById("syncModal").classList.remove("open");
  }
}

// ---- Strava Modal ----
function openStravaModal(note) {
  document.getElementById("stravaModal").classList.add("open");
  document.getElementById("stravaModalStatus").textContent = "";
  document.getElementById("stravaModalStatus").className = "sync-status";
  const noteEl = document.getElementById("stravaModalNote");
  if (note) {
    noteEl.textContent = note;
    noteEl.className = "strava-modal-note " + (note.startsWith("✓") ? "success" : "error");
  } else {
    noteEl.textContent = "";
    noteEl.className = "strava-modal-note";
  }
  loadStravaSection();
}

function closeStravaModal(e) {
  if (!e || e.target === document.getElementById("stravaModal")) {
    document.getElementById("stravaModal").classList.remove("open");
  }
}

async function startSync() {
  const email = document.getElementById("garminEmail").value.trim();
  const password = document.getElementById("garminPassword").value.trim();
  const days = parseInt(document.getElementById("syncDays").value) || 30;
  const fullHistory = document.getElementById("fullHistoryCheck").checked;
  const mfaCode = document.getElementById("mfaCode").value.trim() || null;

  const statusEl = document.getElementById("syncStatus");
  const btn = document.getElementById("syncConfirmBtn");

  statusEl.textContent = fullHistory
    ? "Fetching full history — this may take a few minutes..."
    : `Syncing last ${days} days...`;
  statusEl.className = "sync-status loading";
  btn.disabled = true;

  try {
    const res = await fetch("/api/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: email || null,
        password: password || null,
        days,
        full_history: fullHistory,
        mfa_code: mfaCode,
      }),
    });

    const data = await res.json();

    if (!res.ok) {
      if (res.status === 449 || data.detail === "MFA_REQUIRED") {
        document.getElementById("mfaGroup").style.display = "";
        document.getElementById("mfaCode").focus();
        statusEl.textContent = "MFA required — enter the 6-digit code from your authenticator app, then click Sync Now again.";
        statusEl.className = "sync-status error";
      } else {
        statusEl.textContent = `Error: ${data.detail}`;
        statusEl.className = "sync-status error";
      }
      return;
    }

    const errs = data.errors?.length ? ` (${data.errors.length} minor errors)` : "";
    statusEl.textContent = `✓ Synced ${data.activities} runs, ${data.sleep_days} sleep days · ${data.indexed_in_vector_store} activities indexed${errs}`;
    statusEl.className = "sync-status success";

    setTimeout(() => {
      closeGarminModal();
      loadStats();
      loadActivities();
    }, 2000);

  } catch (e) {
    statusEl.textContent = `Network error: ${e.message}`;
    statusEl.className = "sync-status error";
  } finally {
    btn.disabled = false;
  }
}

// ---- Markdown renderer (no dependencies) ----
function renderMarkdown(text) {
  if (!text) return "";
  let html = escapeHtml(text);

  html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
  html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");

  html = html.replace(/```[\s\S]*?```/g, m => {
    const code = m.slice(3, -3).replace(/^[a-z]*\n/, "");
    return `<pre><code>${code}</code></pre>`;
  });

  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");

  html = html.replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");

  html = html.replace(/(\|.+\|\n)(\|[-:| ]+\|\n)((?:\|.+\|\n)*)/g, (_, header, sep, body) => {
    const headers = header.trim().split("|").filter(Boolean).map(h => `<th>${h.trim()}</th>`).join("");
    const rows = body.trim().split("\n").map(row =>
      "<tr>" + row.split("|").filter(Boolean).map(c => `<td>${c.trim()}</td>`).join("") + "</tr>"
    ).join("");
    return `<table><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table>`;
  });

  html = html.replace(/^---+$/gm, "<hr>");

  html = html.replace(/((?:^[-*] .+\n?)+)/gm, block => {
    const items = block.trim().split("\n").map(l => `<li>${l.replace(/^[-*] /, "")}</li>`).join("");
    return `<ul>${items}</ul>`;
  });

  html = html.replace(/((?:^\d+\. .+\n?)+)/gm, block => {
    const items = block.trim().split("\n").map(l => `<li>${l.replace(/^\d+\. /, "")}</li>`).join("");
    return `<ol>${items}</ol>`;
  });

  html = html.replace(/\n\n+/g, "</p><p>");
  html = `<p>${html}</p>`;
  html = html.replace(/<\/p><p>/g, "</p><p>").replace(/\n/g, "<br>");
  html = html.replace(/<p>(<(?:h[123]|ul|ol|pre|table|hr))/g, "$1");
  html = html.replace(/(<\/(?:h[123]|ul|ol|pre|table|hr)>)<\/p>/g, "$1");

  return html;
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val ?? "—";
}

// ---- Chart Modal ----

function openChart(metric, title, defaultDays = 30) {
  _currentMetric = metric;
  _currentDays = defaultDays;
  document.getElementById("chartModalTitle").textContent = title;
  document.querySelectorAll(".period-btn").forEach(btn => {
    btn.classList.toggle("active", parseInt(btn.dataset.days) === defaultDays);
  });
  document.getElementById("chartModal").classList.add("open");
  loadChartData(metric, defaultDays);
}

function closeChart(e) {
  if (!e || e.target === document.getElementById("chartModal")) {
    document.getElementById("chartModal").classList.remove("open");
    if (_chartInstance) { _chartInstance.destroy(); _chartInstance = null; }
  }
}

function setChartPeriod(days) {
  _currentDays = days;
  document.querySelectorAll(".period-btn").forEach(btn => {
    btn.classList.toggle("active", parseInt(btn.dataset.days) === days);
  });
  loadChartData(_currentMetric, days);
}

async function loadChartData(metric, days) {
  const statsBar = document.getElementById("chartStatsBar");
  statsBar.innerHTML = '<span style="color:var(--text-muted);font-size:12px">Loading…</span>';

  try {
    const res = await fetch(`/api/chart-data?metric=${metric}&days=${days}`);
    if (!res.ok) throw new Error("Request failed");
    const { data, unit, lower_is_better } = await res.json();

    if (!data || data.length === 0) {
      statsBar.innerHTML = '<span style="color:var(--text-muted);font-size:12px">No data available for this period. Try syncing first.</span>';
      if (_chartInstance) { _chartInstance.destroy(); _chartInstance = null; }
      return;
    }

    renderChart(document.getElementById("chartCanvas"), data, unit, metric, lower_is_better);
    renderChartStats(statsBar, data, unit, lower_is_better);
  } catch (e) {
    statsBar.innerHTML = `<span style="color:var(--red);font-size:12px">Error: ${e.message}</span>`;
  }
}

function _linearRegression(values) {
  const n = values.length;
  if (n < 3) return null;
  let sx = 0, sy = 0, sxy = 0, sx2 = 0;
  for (let i = 0; i < n; i++) { sx += i; sy += values[i]; sxy += i * values[i]; sx2 += i * i; }
  const denom = n * sx2 - sx * sx;
  if (!denom) return null;
  const m = (n * sxy - sx * sy) / denom;
  const b = (sy - m * sx) / n;
  return values.map((_, i) => +(m * i + b).toFixed(4));
}

function renderChart(canvas, data, unit, metric, lowerIsBetter = false) {
  if (_chartInstance) { _chartInstance.destroy(); _chartInstance = null; }

  const BAR_METRICS = new Set(["daily_distance", "weekly_distance", "steps", "intensity_minutes"]);
  const isBar = BAR_METRICS.has(metric);
  const color = _chartColor(metric);
  const labels = data.map(d => d.date);
  const values = data.map(d => d.value);

  const mainDataset = isBar
    ? { data: values, backgroundColor: color + "bb", borderColor: color, borderWidth: 1, borderRadius: 3 }
    : {
        data: values,
        borderColor: color,
        borderWidth: 2,
        backgroundColor: color + "25",
        fill: true,
        tension: 0.35,
        pointRadius: data.length > 30 ? 2 : 4,
        pointHoverRadius: 6,
        pointBackgroundColor: color,
      };

  const trendValues = _linearRegression(values);
  const datasets = [mainDataset];
  if (trendValues) {
    datasets.push({
      type: "line",
      data: trendValues,
      borderColor: color + "90",
      borderWidth: 2,
      borderDash: [5, 4],
      pointRadius: 0,
      pointHoverRadius: 0,
      fill: false,
      tension: 0,
      label: "_trend",
    });
  }

  _chartInstance = new Chart(canvas.getContext("2d"), {
    type: isBar ? "bar" : "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          filter: item => item.dataset.label !== "_trend",
          backgroundColor: "rgba(26,29,39,.97)",
          borderColor: "#2d3148",
          borderWidth: 1,
          titleColor: "#94a3b8",
          bodyColor: "#e2e8f0",
          callbacks: {
            label: ctx => {
              const v = ctx.parsed.y;
              if (unit === "min/km") {
                const m = Math.floor(v), s = Math.round((v - m) * 60);
                return `${m}:${s.toString().padStart(2, "0")} /km`;
              }
              return `${typeof v === "number" ? v.toLocaleString() : v} ${unit}`;
            },
          },
        },
      },
      scales: {
        x: {
          grid: { color: "#2d314830" },
          ticks: { color: "#6b7280", maxTicksLimit: 10, font: { size: 11 } },
        },
        y: {
          grid: { color: "#2d314830" },
          reverse: lowerIsBetter,
          ticks: {
            color: "#6b7280",
            font: { size: 11 },
            callback: v => {
              if (unit === "min/km") {
                const m = Math.floor(v), s = Math.round((v - m) * 60);
                return `${m}:${s.toString().padStart(2, "0")}`;
              }
              if (unit === "steps") return (v / 1000).toFixed(0) + "k";
              return v;
            },
          },
        },
      },
    },
  });
}

function renderChartStats(el, data, unit, lowerIsBetter) {
  const values = data.map(d => d.value);
  const avg = values.reduce((a, b) => a + b, 0) / values.length;
  const min = Math.min(...values);
  const max = Math.max(...values);

  const mid = Math.max(1, Math.floor(values.length / 2));
  const recentAvg = values.slice(mid).reduce((a, b) => a + b, 0) / (values.length - mid);
  const olderAvg = values.slice(0, mid).reduce((a, b) => a + b, 0) / mid;
  const improving = lowerIsBetter ? recentAvg < olderAvg : recentAvg > olderAvg;
  const trendColor = improving ? "var(--green)" : "var(--red)";
  const trendLabel = improving ? "↑ improving" : "↓ declining";

  const fmt = v => {
    if (unit === "min/km") {
      const m = Math.floor(v), s = Math.round((v - m) * 60);
      return `${m}:${s.toString().padStart(2, "0")}`;
    }
    if (unit === "steps") return Math.round(v).toLocaleString();
    return typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(1)) : v;
  };

  el.innerHTML = `
    <div class="chart-stat">
      <div class="chart-stat-label">Avg</div>
      <div class="chart-stat-value">${fmt(avg)} <span style="font-size:10px;color:var(--text-muted);font-weight:400">${unit}</span></div>
    </div>
    <div class="chart-stat">
      <div class="chart-stat-label">Min</div>
      <div class="chart-stat-value">${fmt(min)}</div>
    </div>
    <div class="chart-stat">
      <div class="chart-stat-label">Max</div>
      <div class="chart-stat-value">${fmt(max)}</div>
    </div>
    <div class="chart-stat">
      <div class="chart-stat-label">Trend</div>
      <div class="chart-stat-value" style="color:${trendColor};font-size:13px">${trendLabel}</div>
    </div>
  `;
}

// ---- Performance Modal ----

function openPerfModal() {
  document.getElementById("perfModal").classList.add("open");
  loadPerfData();
}

function closePerfModal(e) {
  if (!e || e.target === document.getElementById("perfModal")) {
    document.getElementById("perfModal").classList.remove("open");
  }
}

async function loadPerfData() {
  try {
    const res = await fetch("/api/performance");
    if (!res.ok) return;
    const d = await res.json();

    // VO2 Max
    const vo2El = document.getElementById("perfVo2");
    const vo2DateEl = document.getElementById("perfVo2Date");
    if (d.vo2_max) {
      vo2El.textContent = d.vo2_max.vo2_max?.toFixed(1) ?? "—";
      vo2DateEl.textContent = d.vo2_max.date ?? "";
    } else {
      vo2El.textContent = "—";
      vo2DateEl.textContent = "Not synced yet";
    }

    // Race predictions
    const noRace = document.getElementById("perfNoRace");
    const raceDate = document.getElementById("perfRaceDate");
    const raceGrid = document.querySelector(".perf-race-grid");
    if (d.race_predictions) {
      noRace.style.display = "none";
      raceGrid.style.display = "";
      raceDate.textContent = d.race_predictions.date ? `as of ${d.race_predictions.date}` : "";
      setText("perf5k", d.race_predictions["5k"] ?? "—");
      setText("perf10k", d.race_predictions["10k"] ?? "—");
      setText("perfHalf", d.race_predictions.half ?? "—");
      setText("perfMarathon", d.race_predictions.marathon ?? "—");
    } else {
      noRace.style.display = "";
      raceGrid.style.display = "none";
    }

    // Readiness history
    const listEl = document.getElementById("perfReadinessChart");
    if (d.readiness_history && d.readiness_history.length) {
      listEl.innerHTML = d.readiness_history.map(r => {
        const score = r.training_readiness_score ?? 0;
        const level = (r.training_readiness_level || "").toLowerCase();
        const color = level === "optimal" || level === "good" ? "var(--green)"
          : level === "moderate" ? "var(--yellow)"
          : level === "low" || level === "poor" ? "var(--red)"
          : "var(--accent)";
        return `<div class="perf-readiness-row">
          <span class="perf-readiness-date">${r.date}</span>
          <div class="perf-readiness-bar-wrap">
            <div class="perf-readiness-bar" style="width:${score}%;background:${color}"></div>
          </div>
          <span class="perf-readiness-score" style="color:${color}">${score}</span>
        </div>`;
      }).join("");
    } else {
      listEl.innerHTML = '<span style="color:var(--text-muted);font-size:12px">No readiness data yet.</span>';
    }
  } catch (e) {
    console.warn("perf load failed:", e);
  }
}

// ---- Strava ----

function handleStravaCallback() {
  const params = new URLSearchParams(window.location.search);
  if (params.has("strava_connected")) {
    window.history.replaceState({}, "", "/");
    openStravaModal("✓ Strava connected! Now sync your activities below.");
  } else if (params.has("strava_error")) {
    window.history.replaceState({}, "", "/");
    openStravaModal("Connection failed. Check your STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET in .env.");
  }
}

async function loadStravaSection() {
  const el = document.getElementById("stravaModalContent");
  el.innerHTML = '<span style="font-size:12px;color:var(--text-muted)">Loading…</span>';
  try {
    const res = await fetch("/api/strava/status");
    const d = await res.json();
    window._stravaConnected = d.connected;
    window._stravaAthlete = d.athlete_name || null;
    updateStravaBtn();

    if (d.connected) {
      el.innerHTML = `
        <div class="strava-connected-row">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="#FC4C02"><path d="M15.387 17.944l-2.089-4.116h-3.065L15.387 24l5.15-10.172h-3.066m-7.008-5.599l2.836 5.598h4.172L10.463 0l-7 13.828h4.169"/></svg>
          ${escapeHtml(d.athlete_name || "Strava connected")}
        </div>
        <p class="strava-modal-desc">Sync your runs from Strava. Distance, pace, HR, elevation, and cadence will be imported.</p>
        <div class="form-group">
          <label>Days to sync</label>
          <input id="stravaDays" type="number" class="form-input" value="30" min="1" max="365" />
        </div>
        <button class="strava-btn" id="stravaSyncBtn" onclick="syncStrava()">Sync Activities</button>`;
    } else {
      el.innerHTML = `
        <p class="strava-modal-desc">Connect your Strava account to import runs. You'll get distance, pace, heart rate, elevation, and cadence data.</p>
        <p class="strava-modal-desc" style="margin-top:6px;font-size:11px">Note: Strava doesn't provide sleep, HRV, or training readiness — those are Garmin-only metrics.</p>
        <a href="/api/strava/auth" class="strava-btn" style="margin-top:12px">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="white" style="vertical-align:-2px;margin-right:6px"><path d="M15.387 17.944l-2.089-4.116h-3.065L15.387 24l5.15-10.172h-3.066m-7.008-5.599l2.836 5.598h4.172L10.463 0l-7 13.828h4.169"/></svg>
          Connect with Strava</a>`;
    }
  } catch (e) {
    el.innerHTML = '<span style="font-size:12px;color:var(--text-muted)">Could not load Strava status.</span>';
  }
}

function updateStravaBtn() {
  const btn = document.getElementById("stravaBtn");
  if (!btn) return;
  if (window._stravaConnected) {
    btn.classList.add("connected");
    btn.title = window._stravaAthlete ? `Strava: ${window._stravaAthlete}` : "Strava connected";
  } else {
    btn.classList.remove("connected");
    btn.title = "Connect or sync Strava";
  }
}

async function syncStrava() {
  const days = parseInt(document.getElementById("stravaDays")?.value) || 30;
  const statusEl = document.getElementById("stravaModalStatus");
  const btn = document.getElementById("stravaSyncBtn");

  statusEl.textContent = `Syncing last ${days} days from Strava…`;
  statusEl.className = "sync-status loading";
  btn.disabled = true;

  try {
    const res = await fetch(`/api/strava/sync?days=${days}`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      statusEl.textContent = `Error: ${data.detail}`;
      statusEl.className = "sync-status error";
      return;
    }
    const errs = data.errors?.length ? ` (${data.errors.length} errors)` : "";
    statusEl.textContent = `✓ Synced ${data.activities} runs from Strava · ${data.indexed_in_vector_store} indexed${errs}`;
    statusEl.className = "sync-status success";
    setTimeout(() => { closeStravaModal(); loadStats(); loadActivities(); }, 2000);
  } catch (e) {
    statusEl.textContent = `Network error: ${e.message}`;
    statusEl.className = "sync-status error";
  } finally {
    btn.disabled = false;
  }
}

// ---- Sidebar resize ----

function initSidebarResize() {
  const handle = document.querySelector('.sidebar-divider');
  const grid = document.querySelector('.stats-grid');
  if (!handle || !grid) return;

  const saved = localStorage.getItem('cc_gridH');
  const naturalH = grid.offsetHeight;
  grid.style.height = (saved !== null ? parseInt(saved) : naturalH) + 'px';

  let startY, startH;

  handle.addEventListener('mousedown', e => {
    e.preventDefault();
    startY = e.clientY;
    startH = parseInt(grid.style.height) || grid.offsetHeight;
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';

    function onMove(e) {
      const sidebar = handle.closest('.sidebar');
      const header = sidebar.querySelector('.sidebar-header');
      const maxH = sidebar.offsetHeight - header.offsetHeight - handle.offsetHeight - 60;
      const newH = Math.max(70, Math.min(startH + (e.clientY - startY), maxH));
      grid.style.height = newH + 'px';
    }

    function onUp() {
      localStorage.setItem('cc_gridH', parseInt(grid.style.height));
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    }

    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
}

// ---- Card drag-and-drop ----

function initCardDrag() {
  const grid = document.querySelector('.stats-grid');
  if (!grid) return;

  try {
    const savedOrder = JSON.parse(localStorage.getItem('cc_cardOrder') || 'null');
    if (savedOrder) {
      const cards = [...grid.querySelectorAll('.stat-card[data-card]')];
      savedOrder.forEach(id => {
        const card = cards.find(c => c.dataset.card === id);
        if (card) grid.appendChild(card);
      });
    }
  } catch (_) {}

  let dragSrc = null;
  let dragOverEl = null;

  grid.addEventListener('dragstart', e => {
    const card = e.target.closest('.stat-card[data-card]');
    if (!card) return;
    dragSrc = card;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', card.dataset.card);
    setTimeout(() => card.classList.add('dragging'), 0);
  });

  grid.addEventListener('dragover', e => {
    e.preventDefault();
    const card = e.target.closest('.stat-card[data-card]');
    if (dragOverEl && dragOverEl !== card) dragOverEl.classList.remove('drag-over');
    if (!card || card === dragSrc) { dragOverEl = null; return; }
    dragOverEl = card;
    card.classList.add('drag-over');
    e.dataTransfer.dropEffect = 'move';
  });

  grid.addEventListener('dragleave', e => {
    if (!grid.contains(e.relatedTarget)) {
      if (dragOverEl) { dragOverEl.classList.remove('drag-over'); dragOverEl = null; }
    }
  });

  grid.addEventListener('drop', e => {
    e.preventDefault();
    if (!dragSrc || !dragOverEl || dragSrc === dragOverEl) return;
    const rect = dragOverEl.getBoundingClientRect();
    if (e.clientY < rect.top + rect.height / 2) {
      grid.insertBefore(dragSrc, dragOverEl);
    } else {
      grid.insertBefore(dragSrc, dragOverEl.nextSibling);
    }
    dragOverEl.classList.remove('drag-over');
    saveCardOrder();
  });

  grid.addEventListener('dragend', () => {
    if (dragSrc) dragSrc.classList.remove('dragging');
    if (dragOverEl) dragOverEl.classList.remove('drag-over');
    dragSrc = null;
    dragOverEl = null;
  });
}

function saveCardOrder() {
  const cards = [...document.querySelectorAll('.stats-grid .stat-card[data-card]')];
  localStorage.setItem('cc_cardOrder', JSON.stringify(cards.map(c => c.dataset.card)));
}

// ---- Activity Detail Modal ----

let _currentActivity = null;

async function openActivityModal(activityId) {
  document.getElementById("activityModal").classList.add("open");
  document.getElementById("actDetailGrid").innerHTML =
    '<div style="color:var(--text-muted);padding:20px;text-align:center;grid-column:1/-1">Loading…</div>';
  try {
    const res = await fetch(`/api/activities/${activityId}`);
    if (!res.ok) throw new Error("Not found");
    _currentActivity = await res.json();
    renderActivityDetail(_currentActivity);
  } catch (e) {
    document.getElementById("actDetailGrid").innerHTML =
      `<div style="color:var(--red);padding:20px;grid-column:1/-1">Error loading activity: ${e.message}</div>`;
  }
}

function closeActivityModal(e) {
  if (!e || e.target === document.getElementById("activityModal")) {
    document.getElementById("activityModal").classList.remove("open");
    _currentActivity = null;
  }
}

function renderActivityDetail(d) {
  const raw = d.raw_data || {};
  document.getElementById("actDetailBadge").textContent = fmt_type(d.activity_type);
  document.getElementById("actDetailName").textContent = d.name || "Activity";
  document.getElementById("actDetailDate").textContent = (d.start_time || "").slice(0, 16).replace("T", " ");

  const fmtPace = p => p > 0 ? `${Math.floor(p)}:${String(Math.round((p % 1) * 60)).padStart(2, "0")}/km` : null;
  const fmtDur = s => {
    if (!s || s <= 0) return null;
    const h = Math.floor(s / 3600), rem = s % 3600;
    return h ? `${h}h ${Math.floor(rem / 60)}m ${Math.round(rem % 60)}s` : `${Math.floor(rem / 60)}m ${Math.round(rem % 60)}s`;
  };
  const fmtSpd = ms => ms > 0 ? `${(ms * 3.6).toFixed(1)} km/h` : null;
  const pos = v => v != null && Number(v) > 0;

  const sections = [
    {
      title: "Overview",
      items: [
        ["Distance", pos(d.distance_meters) ? `${(d.distance_meters / 1000).toFixed(2)} km` : null],
        ["Duration", fmtDur(d.duration_seconds)],
        ["Moving Time", fmtDur(raw.movingDuration)],
        ["Calories", pos(d.calories) ? `${d.calories} kcal` : null],
        ["Location", raw.locationName || null],
      ],
    },
    {
      title: "Pace & Speed",
      items: [
        ["Avg Pace", fmtPace(d.avg_pace_per_km)],
        ["Avg Speed", fmtSpd(raw.avgSpeed)],
        ["Max Speed", fmtSpd(raw.maxSpeed)],
      ],
    },
    {
      title: "Heart Rate",
      items: [
        ["Avg HR", pos(d.avg_heart_rate) ? `${d.avg_heart_rate} bpm` : null],
        ["Max HR", pos(d.max_heart_rate) ? `${d.max_heart_rate} bpm` : null],
      ],
    },
    {
      title: "Elevation",
      items: [
        ["Gain", pos(d.elevation_gain) ? `${Math.round(d.elevation_gain)} m` : null],
        ["Loss", pos(raw.elevationLoss) ? `${Math.round(raw.elevationLoss)} m` : null],
        ["Min", raw.minElevation != null ? `${Math.round(raw.minElevation)} m` : null],
        ["Max", raw.maxElevation != null ? `${Math.round(raw.maxElevation)} m` : null],
      ],
    },
    {
      title: "Cadence",
      items: [
        ["Avg", pos(raw.averageRunningCadenceInStepsPerMinute) ? `${Math.round(raw.averageRunningCadenceInStepsPerMinute)} spm` : null],
        ["Max", pos(raw.maxRunningCadenceInStepsPerMinute) ? `${Math.round(raw.maxRunningCadenceInStepsPerMinute)} spm` : null],
      ],
    },
    {
      title: "Power",
      items: [
        ["Avg", pos(d.avg_power) ? `${d.avg_power} W` : null],
        ["Norm", pos(raw.normPower) ? `${Math.round(raw.normPower)} W` : null],
        ["Max", pos(raw.maxPower) ? `${Math.round(raw.maxPower)} W` : null],
      ],
    },
    {
      title: "Running Dynamics",
      items: [
        ["Vert. Osc.", pos(d.avg_vertical_oscillation) ? `${d.avg_vertical_oscillation.toFixed(1)} cm` : null],
        ["Ground Contact", pos(d.avg_ground_contact_time) ? `${Math.round(d.avg_ground_contact_time)} ms` : null],
        ["Stride Length", pos(d.avg_stride_length) ? `${d.avg_stride_length.toFixed(2)} m` : null],
      ],
    },
    {
      title: "Training Load",
      items: [
        ["TSS", pos(d.training_stress_score) ? Math.round(d.training_stress_score) : null],
        ["Aerobic Effect", pos(d.aerobic_training_effect) ? d.aerobic_training_effect.toFixed(1) : null],
        ["Anaerobic Effect", pos(d.anaerobic_training_effect) ? d.anaerobic_training_effect.toFixed(1) : null],
        ["VO2 Max", pos(raw.vO2MaxValue) ? `${parseFloat(raw.vO2MaxValue).toFixed(1)} ml/kg/min` : null],
      ],
    },
  ];

  const filtered = sections
    .map(s => ({ ...s, items: s.items.filter(([, v]) => v != null) }))
    .filter(s => s.items.length > 0);

  document.getElementById("actDetailGrid").innerHTML = filtered.map(s => `
    <div class="act-detail-section">
      <div class="act-detail-section-title">${s.title}</div>
      ${s.items.map(([label, value]) => `
        <div class="act-detail-row">
          <span class="act-detail-label">${label}</span>
          <span class="act-detail-value">${escapeHtml(String(value))}</span>
        </div>`).join("")}
    </div>`).join("");
}

function askAboutCurrentActivity() {
  if (!_currentActivity) return;
  const d = _currentActivity;
  const date = (d.start_time || "").slice(0, 10);
  const km = d.distance_meters ? `${(d.distance_meters / 1000).toFixed(2)}km` : "unknown distance";
  closeActivityModal();
  const input = document.getElementById("messageInput");
  input.value = `Analyze my ${km} run on ${date}. What does it tell you about my current fitness?`;
  input.focus();
  autoResize(input);
}

function _chartColor(metric) {
  const colors = {
    sleep_score: "#34d399",
    hrv: "#4f8ef7",
    resting_hr: "#fbbf24",
    training_readiness: "#34d399",
    vo2_max: "#4f8ef7",
    steps: "#fbbf24",
    intensity_minutes: "#34d399",
    daily_distance: "#4f8ef7",
    weekly_distance: "#4f8ef7",
    pace: "#f87171",
  };
  return colors[metric] || "#4f8ef7";
}
