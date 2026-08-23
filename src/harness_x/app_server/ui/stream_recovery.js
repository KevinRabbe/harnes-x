"use strict";

if (typeof scheduleReconnect !== "function") {
  throw new Error("Harness X stream reconnect function did not load");
}
if (typeof abortStreams !== "function") {
  throw new Error("Harness X stream abort function did not load");
}

const m46ScheduleReconnect = scheduleReconnect;
const m46AbortStreams = abortStreams;

const streamRecoveryState = {
  lifecycle: null,
  trace: null,
  inFlight: null,
};

function streamRecoveryEntryIsCurrent(entry) {
  return Boolean(
    entry
      && selectionIsCurrent(entry.sessionId, entry.generation)
      && Number.isSafeInteger(entry.cursor)
      && entry.cursor >= 0,
  );
}

function currentStreamRecovery(kind) {
  const entry = streamRecoveryState[kind];
  return streamRecoveryEntryIsCurrent(entry) ? entry : null;
}

function updateStreamRecoveryButton() {
  const button = byId("reconnect-streams");
  const selected = state.selectedSessionId ? state.sessions.get(state.selectedSessionId) : null;
  const knownTerminal = selected ? streamPolicy.isTerminalStatus(selected.status) : false;
  const recoverable = Boolean(
    state.token
      && state.selectedSessionId
      && !knownTerminal
      && (currentStreamRecovery("lifecycle") || currentStreamRecovery("trace")),
  );
  button.disabled = !recoverable || streamRecoveryState.inFlight !== null;
  button.classList.toggle("hidden", !recoverable);
}

function clearStreamRecovery(kind) {
  streamRecoveryState[kind] = null;
  updateStreamRecoveryButton();
}

function clearAllStreamRecovery() {
  streamRecoveryState.lifecycle = null;
  streamRecoveryState.trace = null;
  updateStreamRecoveryButton();
}

function markStreamRecoverable(kind, sessionId, cursor, generation) {
  if (!selectionIsCurrent(sessionId, generation)) return;
  if (!Number.isSafeInteger(cursor) || cursor < 0) return;
  const selected = state.sessions.get(sessionId);
  if (selected && streamPolicy.isTerminalStatus(selected.status)) {
    clearStreamRecovery(kind);
    setPill(streamPillId(kind), "Closed", "muted");
    return;
  }
  streamRecoveryState[kind] = Object.freeze({ sessionId, cursor, generation });
  byId("stream-recovery-status").textContent = `${kind === "lifecycle" ? "Lifecycle" : "Trace"} automatic reconnect exhausted; manual reconnect is available.`;
  updateStreamRecoveryButton();
}

scheduleReconnect = function scheduleReconnectWithManualRecovery(
  kind,
  sessionId,
  cursor,
  generation,
  consecutiveFailures,
  restart,
) {
  const delay = streamPolicy.reconnectDelayMs(consecutiveFailures - 1);
  m46ScheduleReconnect(kind, sessionId, cursor, generation, consecutiveFailures, restart);
  if (delay == null && selectionIsCurrent(sessionId, generation)) {
    markStreamRecoverable(kind, sessionId, cursor, generation);
  }
};

abortStreams = function abortStreamsWithRecoveryReset() {
  clearAllStreamRecovery();
  streamRecoveryState.inFlight = null;
  byId("stream-recovery-status").textContent = "";
  m46AbortStreams();
};

async function recoverDisconnectedStreams() {
  if (streamRecoveryState.inFlight !== null) return;
  if (!state.token || !state.selectedSessionId) return;
  if (!currentStreamRecovery("lifecycle") && !currentStreamRecovery("trace")) return;

  const sessionId = state.selectedSessionId;
  const generation = state.selectionGeneration;
  const attempt = Object.freeze({ sessionId, generation });
  streamRecoveryState.inFlight = attempt;
  byId("stream-recovery-status").textContent = "Checking selected session before reconnect…";
  updateStreamRecoveryButton();

  try {
    const snapshot = await api(`/v1/sessions/${encodeURIComponent(sessionId)}`);
    if (!selectionIsCurrent(sessionId, generation)) return;

    renderSnapshot(snapshot);
    renderSessions([...state.sessions.values()]);

    if (streamPolicy.isTerminalStatus(snapshot.status)) {
      abortStreams();
      setPill("lifecycle-state", "Closed", "muted");
      setPill("trace-state", "Closed", "muted");
      byId("stream-recovery-status").textContent = "Session is terminal; live streams are closed.";
      return;
    }

    const lifecycle = currentStreamRecovery("lifecycle");
    const trace = currentStreamRecovery("trace");
    const restarted = [];

    if (lifecycle) {
      clearStreamRecovery("lifecycle");
      restarted.push("lifecycle");
      void runLifecycleStream(sessionId, lifecycle.cursor, generation, 0);
    }
    if (trace) {
      clearStreamRecovery("trace");
      restarted.push("trace");
      void runTraceStream(sessionId, trace.cursor, generation, 0);
    }

    if (restarted.length) {
      byId("stream-recovery-status").textContent = `Reconnected ${restarted.join(" + ")} stream${restarted.length === 1 ? "" : "s"}.`;
    }
  } catch (error) {
    if (!selectionIsCurrent(sessionId, generation)) return;
    if (nonRetriableStreamError(error)) {
      clearAllStreamRecovery();
      byId("stream-recovery-status").textContent = `Reconnect rejected: ${messageFromError(error)}`;
    } else {
      byId("stream-recovery-status").textContent = `Reconnect check failed: ${messageFromError(error)}`;
    }
  } finally {
    if (streamRecoveryState.inFlight === attempt) {
      streamRecoveryState.inFlight = null;
      updateStreamRecoveryButton();
    }
  }
}

byId("reconnect-streams").addEventListener("click", () => {
  void recoverDisconnectedStreams();
});

window.addEventListener("beforeunload", () => {
  streamRecoveryState.lifecycle = null;
  streamRecoveryState.trace = null;
  streamRecoveryState.inFlight = null;
});

updateStreamRecoveryButton();
