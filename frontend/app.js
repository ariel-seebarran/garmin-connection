// ---- State ----
const state = {
  messages: [],
  streaming: false,
  abortController: null,
};

// ---- Init ----
document.addEventListener("DOMContentLoaded", () => {
  loadStats();
  loadActivities();
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

    const syncEl = document.getElementById("lastSync");
    if (d.last_sync) {
      syncEl.textContent = `Last sync: ${d.last_sync}`;
    }
    if (d.total_indexed) {
      syncEl.textContent += ` · ${d.total_indexed} runs indexed`;
    }
  } catch (e) {
    console.warn("Stats load failed:", e);
  }
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
    <div class="activity-item" onclick="askAboutActivity('${a.date}', '${a.distance_km}km')">
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
  const goal = document.getElementById("goalInput").value.trim() || null;
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

// ---- Sync Modal ----
function openSyncModal() {
  document.getElementById("syncModal").classList.add("open");
  document.getElementById("syncStatus").textContent = "";
  document.getElementById("syncStatus").className = "sync-status";
  document.getElementById("mfaGroup").style.display = "none";
  document.getElementById("mfaCode").value = "";
}

function closeSyncModal(e) {
  if (!e || e.target === document.getElementById("syncModal")) {
    document.getElementById("syncModal").classList.remove("open");
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
      closeSyncModal();
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
