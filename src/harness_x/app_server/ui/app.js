"use strict";

const streamPolicy = globalThis.HarnessXStreamPolicy;
if (!streamPolicy) throw new Error("Harness X stream policy did not load");

const state = {
  token: null,
  selectedSessionId: null,
  selectionGeneration: 0,
  sessions: new Map(),
  streamControllers: new Set(),
  streamReconnectTimers: new Set(),
  lifecycleSequences: new Set(),
  traceSteps: new Set(),
};

const byId = (id) => document.getElementById(id);

function setText(id, value) {
  byId(id).textContent = value == null || value === "" ? "—" : String(value);
}

function statusClass(status) {
  return `pill pill--${String(status || "muted").replaceAll(" ", "_")}`;
}

function setPill(id, text, status = "muted") {
  const element = byId(id);
  element.className = statusClass(status);
  element.textContent = text;
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function messageFromError(error) {
  if (error instanceof Error) return error.message;
  return String(error);
}

async function healthCheck() {
  const dot = byId("health-dot");
  const text = byId("health-text");
  try {
    const response = await fetch("/v1/health", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`health HTTP ${response.status}`);
    const health = await response.json();
    dot.className = "health-dot health-dot--ok";
    text.textContent = `Local server online · ${health.active_sessions}/${health.total_sessions} active/total`;
  } catch (error) {
    dot.className = "health-dot health-dot--bad";
    text.textContent = `Local server unavailable · ${messageFromError(error)}`;
  }
}

async function api(path, options = {}) {
  if (!state.token) throw new Error("operator view is locked");
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${state.token}`);
  headers.set("Accept", "application/json");
  if (options.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (_error) {
      payload = null;
    }
  }
  if (!response.ok) {
    const detail = payload && (payload.detail || payload.error);
    const error = new Error(detail || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

function abortStreams() {
  for (const controller of state.streamControllers) controller.abort();
  state.streamControllers.clear();
  for (const timer of state.streamReconnectTimers) clearTimeout(timer);
  state.streamReconnectTimers.clear();
  setPill("trace-state", "Idle");
  setPill("lifecycle-state", "Idle");
}

function lockOperator() {
  abortStreams();
  state.selectionGeneration += 1;
  state.token = null;
  state.selectedSessionId = null;
  state.sessions.clear();
  state.lifecycleSequences.clear();
  state.traceSteps.clear();
  byId("token").value = "";
  byId("lock-button").classList.add("hidden");
  byId("auth-form").classList.remove("hidden");
  byId("refresh-sessions").disabled = true;
  byId("new-session-fields").disabled = true;
  setPill("auth-state", "Locked");
  byId("session-list").replaceChildren(paragraph("Unlock the operator view to load sessions.", "muted small"));
  byId("session-view").classList.add("hidden");
  byId("empty-state").classList.remove("hidden");
}

function paragraph(text, className = "") {
  const element = document.createElement("p");
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

async function unlockOperator(token) {
  state.token = token;
  try {
    await loadSessions();
  } catch (error) {
    state.token = null;
    throw error;
  }
  byId("auth-form").classList.add("hidden");
  byId("lock-button").classList.remove("hidden");
  byId("refresh-sessions").disabled = false;
  byId("new-session-fields").disabled = false;
  setPill("auth-state", "Unlocked", "succeeded");
}

function renderSessions(items) {
  const root = byId("session-list");
  root.replaceChildren();
  if (!items.length) {
    root.append(paragraph("No sessions yet.", "muted small"));
    return;
  }
  const ordered = [...items].sort((left, right) => String(right.created_at).localeCompare(String(left.created_at)));
  for (const session of ordered) {
    state.sessions.set(session.session_id, session);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "session-item";
    if (session.session_id === state.selectedSessionId) button.classList.add("session-item--selected");
    button.addEventListener("click", () => selectSession(session.session_id));

    const top = document.createElement("span");
    top.className = "session-item__top";
    const profile = document.createElement("strong");
    profile.textContent = session.request.model_profile;
    const status = document.createElement("span");
    status.className = statusClass(session.status);
    status.textContent = session.status;
    top.append(profile, status);

    const task = document.createElement("span");
    task.className = "session-item__task";
    task.textContent = session.request.task;

    const id = document.createElement("span");
    id.className = "session-item__id mono";
    id.textContent = session.session_id;
    button.append(top, task, id);
    root.append(button);
  }
}

async function loadSessions() {
  const page = await api("/v1/sessions?limit=200");
  renderSessions(page.sessions || []);
  return page;
}

function addMetadata(root, label, value) {
  const wrapper = document.createElement("div");
  const term = document.createElement("dt");
  term.textContent = label;
  const detail = document.createElement("dd");
  detail.textContent = value || "—";
  wrapper.append(term, detail);
  root.append(wrapper);
}

function renderSnapshot(snapshot) {
  state.sessions.set(snapshot.session_id, snapshot);
  byId("empty-state").classList.add("hidden");
  byId("session-view").classList.remove("hidden");
  setText("session-heading", snapshot.request.task.split("\n", 1)[0].slice(0, 100) || "Session");
  setText("session-id", snapshot.session_id);
  setPill("session-status", snapshot.status, snapshot.status);
  setText("summary-model", snapshot.request.model_profile);
  setText("summary-workspace", snapshot.request.workspace_root);
  setText(
    "summary-trace",
    snapshot.trace_id ? `${snapshot.trace_id} · ${snapshot.trace_path}` : "Not attached",
  );
  setText("summary-report", snapshot.coding_report_path || "Not available");
  setText("task-text", snapshot.request.task);

  const times = byId("session-times");
  times.replaceChildren();
  addMetadata(times, "Created", formatTime(snapshot.created_at));
  addMetadata(times, "Started", formatTime(snapshot.started_at));
  addMetadata(times, "Completed", formatTime(snapshot.completed_at));
  addMetadata(times, "Reasoning budget", String(snapshot.request.max_reasoning_steps));
  addMetadata(times, "Tool budget", String(snapshot.request.max_tool_actions));
  addMetadata(times, "Output token budget", String(snapshot.request.max_output_tokens));

  const failure = byId("failure-reason");
  if (snapshot.failure_reason) {
    failure.textContent = snapshot.failure_reason;
    failure.classList.remove("hidden");
  } else {
    failure.textContent = "";
    failure.classList.add("hidden");
  }

  byId("cancel-session").disabled = !["created", "running"].includes(snapshot.status);
}

function timelineEmpty(root, text) {
  root.append(paragraph(text, "timeline-empty"));
}

function appendTraceEvent(event) {
  if (state.traceSteps.has(event.step)) return;
  state.traceSteps.add(event.step);
  const root = byId("trace-events");
  root.querySelector(".timeline-empty")?.remove();

  const item = document.createElement("article");
  item.className = "timeline-item";
  const top = document.createElement("div");
  top.className = "timeline-item__top";
  const type = document.createElement("span");
  type.className = "timeline-item__type";
  type.textContent = event.event_type;
  const meta = document.createElement("span");
  meta.className = "timeline-item__meta mono";
  meta.textContent = `#${event.step} · ${formatTime(event.timestamp)}`;
  top.append(type, meta);

  const component = document.createElement("div");
  component.className = "timeline-item__component mono";
  component.textContent = event.component;

  const details = document.createElement("pre");
  details.textContent = JSON.stringify({
    metadata: event.metadata,
    input_refs: event.input_refs,
    output_refs: event.output_refs,
    source_event_id: event.source_event_id,
    source_previous_hash: event.source_previous_hash,
    source_event_hash: event.source_event_hash,
    projection_truncated: event.projection_truncated,
  }, null, 2);
  item.append(top, component, details);
  root.append(item);
  root.scrollTop = root.scrollHeight;
}

function appendTraceError(payload) {
  const root = byId("trace-events");
  const item = document.createElement("article");
  item.className = "timeline-item";
  const title = document.createElement("div");
  title.className = "timeline-item__type";
  title.textContent = "trace_corruption";
  const details = document.createElement("pre");
  details.textContent = payload.detail || "Trace stream reported corruption.";
  item.append(title, details);
  root.append(item);
  setPill("trace-state", "Corrupt", "failed");
}

function appendLifecycleEvent(event) {
  if (state.lifecycleSequences.has(event.sequence)) return;
  state.lifecycleSequences.add(event.sequence);
  const root = byId("lifecycle-events");
  root.querySelector(".timeline-empty")?.remove();

  const item = document.createElement("article");
  item.className = "timeline-item";
  const top = document.createElement("div");
  top.className = "timeline-item__top";
  const type = document.createElement("span");
  type.className = "timeline-item__type";
  type.textContent = event.kind;
  const meta = document.createElement("span");
  meta.className = "timeline-item__meta mono";
  meta.textContent = `#${event.sequence} · ${formatTime(event.created_at)}`;
  top.append(type, meta);
  const details = document.createElement("pre");
  details.textContent = JSON.stringify(event.payload || {}, null, 2);
  item.append(top, details);
  root.append(item);
  root.scrollTop = root.scrollHeight;
}

function selectionIsCurrent(sessionId, generation) {
  return state.selectedSessionId === sessionId && state.selectionGeneration === generation;
}

function projectPageCursor(rows, cursorField, declaredNextAfter, append) {
  let cursor = 0;
  for (const row of rows) {
    const sourceCursor = row[cursorField];
    cursor = streamPolicy.advanceCursor(String(sourceCursor), sourceCursor, cursor);
    append(row);
  }
  if (declaredNextAfter !== cursor) {
    throw new Error(
      `page cursor mismatch: rendered through ${cursor}, API declared ${declaredNextAfter}`,
    );
  }
  return cursor;
}

async function loadSessionEvidence(sessionId, generation) {
  if (!selectionIsCurrent(sessionId, generation)) return null;
  state.lifecycleSequences.clear();
  state.traceSteps.clear();
  byId("lifecycle-events").replaceChildren();
  byId("trace-events").replaceChildren();
  timelineEmpty(byId("lifecycle-events"), "No lifecycle events loaded yet.");
  timelineEmpty(byId("trace-events"), "Waiting for an authoritative trace.");

  const encoded = encodeURIComponent(sessionId);
  const [eventPage, tracePage] = await Promise.all([
    api(`/v1/sessions/${encoded}/events?after=0&limit=1000`),
    api(`/v1/sessions/${encoded}/trace?after=0&limit=1000`),
  ]);
  if (!selectionIsCurrent(sessionId, generation)) return null;

  const eventAfter = projectPageCursor(
    eventPage.events || [],
    "sequence",
    eventPage.next_after || 0,
    appendLifecycleEvent,
  );
  const traceAfter = projectPageCursor(
    tracePage.events || [],
    "step",
    tracePage.next_after || 0,
    appendTraceEvent,
  );
  return { eventAfter, traceAfter };
}

function parseSseBlock(block) {
  const event = { id: null, type: "message", data: "" };
  const data = [];
  for (const rawLine of block.split("\n")) {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "id") event.id = value;
    else if (field === "event") event.type = value;
    else if (field === "data") data.push(value);
  }
  event.data = data.join("\n");
  return event;
}

async function streamSse(path, controller, onEvent) {
  const headers = new Headers({
    Authorization: `Bearer ${state.token}`,
    Accept: "text/event-stream",
  });
  const response = await fetch(path, {
    method: "GET",
    headers,
    cache: "no-store",
    signal: controller.signal,
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      detail = payload.detail || payload.error || detail;
    } catch (_error) {
      // Keep the status fallback.
    }
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  if (!response.body) throw new Error("stream response has no readable body");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      if (block.trim()) onEvent(parseSseBlock(block));
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  if (buffer.trim()) onEvent(parseSseBlock(buffer));
}

function streamPillId(kind) {
  return kind === "lifecycle" ? "lifecycle-state" : "trace-state";
}

function nonRetriableStreamError(error) {
  return [400, 401, 403, 404].includes(error && error.status);
}

function releaseStreamController(controller) {
  state.streamControllers.delete(controller);
}

async function selectedSessionIsTerminal(sessionId, generation) {
  const snapshot = await api(`/v1/sessions/${encodeURIComponent(sessionId)}`);
  if (!selectionIsCurrent(sessionId, generation)) return true;
  renderSnapshot(snapshot);
  renderSessions([...state.sessions.values()]);
  return streamPolicy.isTerminalStatus(snapshot.status);
}

function scheduleReconnect(kind, sessionId, cursor, generation, consecutiveFailures, restart) {
  if (!selectionIsCurrent(sessionId, generation)) return;
  const delay = streamPolicy.reconnectDelayMs(consecutiveFailures - 1);
  if (delay == null) {
    setPill(streamPillId(kind), "Disconnected", "failed");
    return;
  }
  setPill(
    streamPillId(kind),
    `Reconnect ${consecutiveFailures}/${streamPolicy.maxReconnectAttempts}`,
    "running",
  );
  const timer = setTimeout(() => {
    state.streamReconnectTimers.delete(timer);
    if (!selectionIsCurrent(sessionId, generation)) return;
    restart(sessionId, cursor, generation, consecutiveFailures);
  }, delay);
  state.streamReconnectTimers.add(timer);
}

async function runLifecycleStream(sessionId, cursor, generation, consecutiveFailures = 0) {
  if (!selectionIsCurrent(sessionId, generation)) return;
  const controller = new AbortController();
  state.streamControllers.add(controller);
  setPill("lifecycle-state", "Live", "running");
  let currentCursor = cursor;
  let failures = consecutiveFailures;
  try {
    await streamSse(
      `/v1/sessions/${encodeURIComponent(sessionId)}/events/stream?after=${currentCursor}`,
      controller,
      (message) => {
        if (!selectionIsCurrent(sessionId, generation)) return;
        if (!message.data) throw new Error("lifecycle SSE event is missing data");
        const payload = JSON.parse(message.data);
        if (message.type !== payload.kind) {
          throw new Error("lifecycle SSE event type does not match payload kind");
        }
        currentCursor = streamPolicy.advanceCursor(message.id, payload.sequence, currentCursor);
        appendLifecycleEvent(payload);
        failures = 0;
      },
    );
    if (!selectionIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    if (await selectedSessionIsTerminal(sessionId, generation)) {
      setPill("lifecycle-state", "Closed", "muted");
      return;
    }
    scheduleReconnect(
      "lifecycle",
      sessionId,
      currentCursor,
      generation,
      failures + 1,
      runLifecycleStream,
    );
  } catch (error) {
    if (!selectionIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    if (nonRetriableStreamError(error)) {
      setPill("lifecycle-state", "Error", "failed");
      console.warn("lifecycle stream rejected reconnect", error);
      return;
    }
    scheduleReconnect(
      "lifecycle",
      sessionId,
      currentCursor,
      generation,
      failures + 1,
      runLifecycleStream,
    );
    console.warn("lifecycle stream interrupted; reconnect scheduled", error);
  } finally {
    releaseStreamController(controller);
  }
}

async function runTraceStream(sessionId, cursor, generation, consecutiveFailures = 0) {
  if (!selectionIsCurrent(sessionId, generation)) return;
  const controller = new AbortController();
  state.streamControllers.add(controller);
  setPill("trace-state", "Live", "running");
  let currentCursor = cursor;
  let failures = consecutiveFailures;
  let traceCorrupt = false;
  try {
    await streamSse(
      `/v1/sessions/${encodeURIComponent(sessionId)}/trace/stream?after=${currentCursor}`,
      controller,
      (message) => {
        if (!selectionIsCurrent(sessionId, generation)) return;
        if (!message.data) throw new Error("trace SSE event is missing data");
        const payload = JSON.parse(message.data);
        if (message.type === "trace_error") {
          appendTraceError(payload);
          traceCorrupt = true;
          return;
        }
        if (message.type !== "trace_event") {
          throw new Error(`unexpected trace SSE event type: ${message.type}`);
        }
        currentCursor = streamPolicy.advanceCursor(message.id, payload.step, currentCursor);
        appendTraceEvent(payload);
        failures = 0;
      },
    );
    if (!selectionIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    if (traceCorrupt) return;
    if (await selectedSessionIsTerminal(sessionId, generation)) {
      setPill("trace-state", "Closed", "muted");
      return;
    }
    scheduleReconnect(
      "trace",
      sessionId,
      currentCursor,
      generation,
      failures + 1,
      runTraceStream,
    );
  } catch (error) {
    if (!selectionIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    if (traceCorrupt) return;
    if (nonRetriableStreamError(error)) {
      setPill("trace-state", "Error", "failed");
      console.warn("trace stream rejected reconnect", error);
      return;
    }
    scheduleReconnect(
      "trace",
      sessionId,
      currentCursor,
      generation,
      failures + 1,
      runTraceStream,
    );
    console.warn("trace stream interrupted; reconnect scheduled", error);
  } finally {
    releaseStreamController(controller);
  }
}

function startStreams(sessionId, eventAfter, traceAfter, generation) {
  if (!selectionIsCurrent(sessionId, generation)) return;
  abortStreams();
  void runLifecycleStream(sessionId, eventAfter, generation);
  void runTraceStream(sessionId, traceAfter, generation);
}

async function refreshSelectedSnapshot() {
  if (!state.selectedSessionId || !state.token) return;
  const encoded = encodeURIComponent(state.selectedSessionId);
  try {
    const snapshot = await api(`/v1/sessions/${encoded}`);
    if (snapshot.session_id !== state.selectedSessionId) return;
    renderSnapshot(snapshot);
    await loadSessions();
  } catch (error) {
    console.warn("failed to refresh selected session", error);
  }
  await healthCheck();
}

async function selectSession(sessionId) {
  if (!state.token) return;
  abortStreams();
  const generation = state.selectionGeneration + 1;
  state.selectionGeneration = generation;
  state.selectedSessionId = sessionId;
  renderSessions([...state.sessions.values()]);
  try {
    const encoded = encodeURIComponent(sessionId);
    const snapshot = await api(`/v1/sessions/${encoded}`);
    if (!selectionIsCurrent(sessionId, generation)) return;
    renderSnapshot(snapshot);
    const cursors = await loadSessionEvidence(sessionId, generation);
    if (!cursors || !selectionIsCurrent(sessionId, generation)) return;
    startStreams(sessionId, cursors.eventAfter, cursors.traceAfter, generation);
  } catch (error) {
    if (!selectionIsCurrent(sessionId, generation)) return;
    byId("trace-events").replaceChildren(paragraph(messageFromError(error), "error-text"));
  }
}

function optionalText(formData, name) {
  const value = String(formData.get(name) || "").trim();
  return value || null;
}

function sessionPayload(form) {
  const data = new FormData(form);
  const commands = String(data.get("verification_commands") || "")
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
  const payload = {
    workspace_root: String(data.get("workspace_root") || "").trim(),
    task: String(data.get("task") || "").trim(),
    model_profile: String(data.get("model_profile") || "").trim(),
    verification_commands: commands,
    max_reasoning_steps: Number.parseInt(String(data.get("max_reasoning_steps")), 10),
    max_tool_actions: Number.parseInt(String(data.get("max_tool_actions")), 10),
    max_output_tokens: Number.parseInt(String(data.get("max_output_tokens")), 10),
    baseline_verification: data.get("baseline_verification") === "on",
    browser_headed: data.get("browser_headed") === "on",
  };
  for (const name of [
    "verification_plan_path",
    "project_memory_root",
    "project_memory_key",
    "browser_application_spec_path",
    "browser_verification_plan_path",
  ]) {
    const value = optionalText(data, name);
    if (value != null) payload[name] = value;
  }
  return payload;
}

byId("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  byId("auth-error").textContent = "";
  const token = byId("token").value.trim();
  byId("token").value = "";
  if (!token) return;
  try {
    await unlockOperator(token);
  } catch (error) {
    byId("auth-error").textContent = `Unlock failed: ${messageFromError(error)}`;
    lockOperator();
  }
});

byId("lock-button").addEventListener("click", lockOperator);
byId("refresh-sessions").addEventListener("click", async () => {
  try {
    await loadSessions();
  } catch (error) {
    byId("auth-error").textContent = `Refresh failed: ${messageFromError(error)}`;
  }
});

byId("new-session-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  byId("create-error").textContent = "";
  try {
    const payload = sessionPayload(event.currentTarget);
    const created = await api("/v1/sessions", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await loadSessions();
    byId("new-session-panel").open = false;
    await selectSession(created.session_id);
  } catch (error) {
    byId("create-error").textContent = `Session creation failed: ${messageFromError(error)}`;
  }
});

byId("cancel-session").addEventListener("click", async () => {
  const sessionId = state.selectedSessionId;
  if (!sessionId) return;
  try {
    const snapshot = await api(`/v1/sessions/${encodeURIComponent(sessionId)}/cancel`, { method: "POST" });
    renderSnapshot(snapshot);
    await loadSessions();
  } catch (error) {
    byId("failure-reason").textContent = `Cancel request failed: ${messageFromError(error)}`;
    byId("failure-reason").classList.remove("hidden");
  }
});

window.addEventListener("beforeunload", abortStreams);
healthCheck();
setInterval(healthCheck, 15000);
