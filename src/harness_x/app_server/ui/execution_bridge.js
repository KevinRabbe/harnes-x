"use strict";

// M69/M70 execution wrapper. The M68 workspace remains the durable Project/Chat projection.
// M69 replaces composer submission; M70 adds a read-only grounded activity projection. Both
// reuse the inherited authenticated `api` binding and never read or export the bearer token.
const conversationExecutionState = {
  pollTimer: null,
  pollGeneration: 0,
  activeExecution: null,
  pendingSubmission: null,
  activityExecutionId: null,
  activityCursor: "a0:t0",
  activityEvents: new Map(),
};

function conversationExecutionSubmissionId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  const suffix = [...bytes]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
  return `submission_${suffix}`;
}

function conversationExecutionEnsureActivityRegion() {
  let region = document.getElementById("daily-work-activity");
  if (region) return region;

  region = document.createElement("section");
  region.id = "daily-work-activity";
  region.className = "daily-inline-form hidden";
  region.setAttribute("aria-live", "polite");

  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "Grounded work activity";
  region.appendChild(eyebrow);

  const list = document.createElement("div");
  list.id = "daily-work-activity-list";
  region.appendChild(list);

  const composerWrap = document.querySelector(".daily-composer-wrap");
  if (!composerWrap || !composerWrap.parentNode) {
    throw new Error("daily composer wrapper is unavailable");
  }
  composerWrap.parentNode.insertBefore(region, composerWrap);
  return region;
}

function conversationExecutionRenderActivity() {
  const region = conversationExecutionEnsureActivityRegion();
  const list = dailyById("daily-work-activity-list");
  list.replaceChildren();
  if (!conversationExecutionState.activityExecutionId) {
    region.classList.add("hidden");
    return;
  }

  region.classList.remove("hidden");
  const events = [...conversationExecutionState.activityEvents.values()];
  if (!events.length) {
    const waiting = document.createElement("p");
    waiting.className = "muted small";
    waiting.textContent = "Waiting for verified App Server or causal-trace activity…";
    list.appendChild(waiting);
    return;
  }

  for (const item of events) {
    const row = document.createElement("p");
    row.className = "muted small";
    row.dataset.activityId = String(item.event_id || "");
    row.dataset.activityKind = String(item.kind || "");
    row.textContent = String(item.summary || item.kind || "Harness X activity");
    list.appendChild(row);
  }
}

function conversationExecutionResetActivity(executionId = null) {
  conversationExecutionState.activityExecutionId = executionId;
  conversationExecutionState.activityCursor = "a0:t0";
  conversationExecutionState.activityEvents = new Map();
  conversationExecutionRenderActivity();
}

function conversationExecutionMergeActivity(page) {
  if (page.execution_id !== conversationExecutionState.activityExecutionId) return;
  for (const item of page.events || []) {
    const eventId = String(item.event_id || "");
    if (!eventId || conversationExecutionState.activityEvents.has(eventId)) continue;
    conversationExecutionState.activityEvents.set(eventId, item);
  }
  conversationExecutionState.activityCursor = String(page.next_cursor || "a0:t0");
  conversationExecutionRenderActivity();
}

function conversationExecutionStopPolling() {
  conversationExecutionState.pollGeneration += 1;
  if (conversationExecutionState.pollTimer != null) {
    clearTimeout(conversationExecutionState.pollTimer);
    conversationExecutionState.pollTimer = null;
  }
  conversationExecutionState.activeExecution = null;
}

function conversationExecutionPath(projectId, chatId, executionId = null) {
  const base = `/v1/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chatId)}/executions`;
  return executionId == null ? base : `${base}/${encodeURIComponent(executionId)}`;
}

function conversationExecutionActivityPath(projectId, chatId, executionId) {
  const cursor = encodeURIComponent(conversationExecutionState.activityCursor);
  return `${conversationExecutionPath(projectId, chatId, executionId)}/activity?cursor=${cursor}&limit=100`;
}

function conversationExecutionCurrent(projectId, chatId) {
  return (
    dailyState.unlocked
    && dailyState.selectedProjectId === projectId
    && dailyState.selectedChatId === chatId
  );
}

function conversationExecutionSetComposerEnabled(enabled) {
  dailyById("daily-send-message").disabled = !enabled;
  dailyById("daily-composer-text").disabled = !enabled;
}

function conversationExecutionStatusText(projection) {
  const link = projection.execution_id || "execution";
  if (projection.status === "created") return `Queued · ${link}`;
  if (projection.status === "running") return `Harness X is working · ${link}`;
  if (projection.status === "cancel_requested") return `Cancellation requested · ${link}`;
  if (projection.status === "succeeded") return `Harness X completed successfully · ${link}`;
  if (projection.status === "failed") return `Harness X could not complete this turn · ${link}`;
  if (projection.status === "cancelled") return `Harness X execution was cancelled · ${link}`;
  return `${String(projection.status || "planned")} · ${link}`;
}

async function conversationExecutionRefreshTerminal(projectId, chatId) {
  if (!conversationExecutionCurrent(projectId, chatId)) return;
  await dailyLoadMessages(chatId);
  if (!conversationExecutionCurrent(projectId, chatId)) return;
  const chat = await api(
    `/v1/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chatId)}`,
  );
  if (!conversationExecutionCurrent(projectId, chatId)) return;
  dailyState.chats.set(chatId, chat);
  dailyRenderChats();
  dailyRenderChatHeader(chat);
}

function conversationExecutionSchedulePoll(
  projectId,
  chatId,
  executionId,
  generation,
  delayMs = 850,
) {
  if (generation !== conversationExecutionState.pollGeneration) return;
  if (!conversationExecutionCurrent(projectId, chatId)) return;
  if (conversationExecutionState.pollTimer != null) clearTimeout(conversationExecutionState.pollTimer);
  conversationExecutionState.pollTimer = setTimeout(() => {
    conversationExecutionState.pollTimer = null;
    void conversationExecutionPoll(projectId, chatId, executionId, generation);
  }, delayMs);
}

async function conversationExecutionPoll(projectId, chatId, executionId, generation) {
  if (generation !== conversationExecutionState.pollGeneration) return;
  if (!conversationExecutionCurrent(projectId, chatId)) return;
  try {
    const page = await api(conversationExecutionActivityPath(projectId, chatId, executionId));
    if (generation !== conversationExecutionState.pollGeneration) return;
    if (!conversationExecutionCurrent(projectId, chatId)) return;
    if (page.execution_id !== executionId) throw new Error("activity execution identity mismatch");

    conversationExecutionMergeActivity(page);
    const projection = {
      execution_id: executionId,
      status: page.status,
      terminal: page.terminal,
    };
    dailyById("daily-composer-status").textContent = conversationExecutionStatusText(projection);

    if (page.terminal && !page.has_more) {
      conversationExecutionState.activeExecution = null;
      conversationExecutionSetComposerEnabled(true);
      await conversationExecutionRefreshTerminal(projectId, chatId);
      return;
    }

    conversationExecutionState.activeExecution = page.terminal ? null : projection;
    conversationExecutionSetComposerEnabled(Boolean(page.terminal));
    conversationExecutionSchedulePoll(
      projectId,
      chatId,
      executionId,
      generation,
      page.has_more ? 25 : 850,
    );
  } catch (error) {
    if (generation !== conversationExecutionState.pollGeneration) return;
    if (!conversationExecutionCurrent(projectId, chatId)) return;
    dailyById("daily-composer-status").textContent = (
      `Execution activity unavailable: ${dailyMessage(error)} · retrying locally`
    );
    conversationExecutionSchedulePoll(projectId, chatId, executionId, generation, 1200);
  }
}

async function conversationExecutionRestore(projectId, chatId) {
  conversationExecutionStopPolling();
  if (!conversationExecutionCurrent(projectId, chatId)) return;
  try {
    const page = await api(conversationExecutionPath(projectId, chatId));
    if (!conversationExecutionCurrent(projectId, chatId)) return;
    const executions = [...(page.executions || [])].sort((left, right) => {
      const byTime = String(left.created_at).localeCompare(String(right.created_at));
      return byTime || String(left.execution_id).localeCompare(String(right.execution_id));
    });
    const latest = executions.length ? executions[executions.length - 1] : null;
    if (!latest) {
      conversationExecutionResetActivity(null);
      conversationExecutionState.activeExecution = null;
      conversationExecutionSetComposerEnabled(true);
      dailyById("daily-composer-status").textContent = "Ready for a Harness X work turn.";
      return;
    }

    conversationExecutionResetActivity(latest.execution_id);
    dailyById("daily-composer-status").textContent = conversationExecutionStatusText(latest);
    conversationExecutionState.activeExecution = latest.terminal ? null : latest;
    conversationExecutionSetComposerEnabled(Boolean(latest.terminal));
    const generation = conversationExecutionState.pollGeneration;
    conversationExecutionSchedulePoll(projectId, chatId, latest.execution_id, generation, 0);
  } catch (error) {
    if (!conversationExecutionCurrent(projectId, chatId)) return;
    conversationExecutionResetActivity(null);
    conversationExecutionState.activeExecution = null;
    conversationExecutionSetComposerEnabled(false);
    dailyById("daily-composer-status").textContent = (
      `Execution state unavailable: ${dailyMessage(error)}`
    );
  }
}

const dailySelectChatBeforeConversationExecution = dailySelectChat;
dailySelectChat = async function dailySelectChatWithConversationExecution(chatId, options) {
  conversationExecutionStopPolling();
  await dailySelectChatBeforeConversationExecution(chatId, options);
  const projectId = dailyState.selectedProjectId;
  if (projectId && dailyState.selectedChatId === chatId) {
    await conversationExecutionRestore(projectId, chatId);
  }
};

const dailyClearChatSelectionBeforeConversationExecution = dailyClearChatSelection;
dailyClearChatSelection = function dailyClearChatSelectionWithConversationExecution() {
  conversationExecutionStopPolling();
  conversationExecutionState.pendingSubmission = null;
  conversationExecutionResetActivity(null);
  dailyClearChatSelectionBeforeConversationExecution();
  conversationExecutionSetComposerEnabled(false);
};

dailyById("daily-composer").addEventListener("submit", async (event) => {
  // Capture-phase interception prevents the frozen M68 local-message submit listener from
  // running for conversation work turns.
  event.preventDefault();
  event.stopImmediatePropagation();

  const projectId = dailyState.selectedProjectId;
  const chatId = dailyState.selectedChatId;
  const textArea = dailyById("daily-composer-text");
  const text = textArea.value;
  if (!projectId || !chatId || !text.trim()) return;
  if (conversationExecutionState.activeExecution) {
    dailyById("daily-composer-status").textContent = "Wait for the active Harness X turn to finish.";
    return;
  }

  let pending = conversationExecutionState.pendingSubmission;
  if (
    !pending
    || pending.projectId !== projectId
    || pending.chatId !== chatId
    || pending.text !== text
  ) {
    pending = {
      projectId,
      chatId,
      text,
      submissionId: conversationExecutionSubmissionId(),
    };
    conversationExecutionState.pendingSubmission = pending;
  }

  conversationExecutionSetComposerEnabled(false);
  dailyById("daily-composer-status").textContent = "Starting Harness X work…";
  try {
    const projection = await api(conversationExecutionPath(projectId, chatId), {
      method: "POST",
      body: JSON.stringify({
        schema_version: "conversation-execution-submit-v1",
        submission_id: pending.submissionId,
        role: "user",
        content: {
          type: "text",
          text,
        },
      }),
    });
    conversationExecutionState.pendingSubmission = null;
    if (!conversationExecutionCurrent(projectId, chatId)) return;
    textArea.value = "";
    await dailyLoadMessages(chatId);
    conversationExecutionResetActivity(projection.execution_id);
    dailyById("daily-composer-status").textContent = conversationExecutionStatusText(projection);
    conversationExecutionState.activeExecution = projection.terminal ? null : projection;
    conversationExecutionSetComposerEnabled(Boolean(projection.terminal));
    const generation = conversationExecutionState.pollGeneration;
    conversationExecutionSchedulePoll(projectId, chatId, projection.execution_id, generation, 0);
  } catch (error) {
    if (error && typeof error.status === "number" && error.status < 500) {
      conversationExecutionState.pendingSubmission = null;
    }
    if (!conversationExecutionCurrent(projectId, chatId)) return;
    const retryNote = conversationExecutionState.pendingSubmission
      ? " Retry will reuse the same submission identity."
      : "";
    dailyById("daily-composer-status").textContent = (
      `Harness X start failed: ${dailyMessage(error)}.${retryNote}`
    );
    conversationExecutionSetComposerEnabled(true);
  } finally {
    if (
      conversationExecutionCurrent(projectId, chatId)
      && !conversationExecutionState.activeExecution
    ) {
      textArea.focus();
    }
  }
}, true);

dailyById("lock-button").addEventListener("click", () => {
  conversationExecutionStopPolling();
  conversationExecutionState.pendingSubmission = null;
  conversationExecutionResetActivity(null);
  conversationExecutionSetComposerEnabled(false);
});

conversationExecutionEnsureActivityRegion();
if (!dailyState.selectedChatId) conversationExecutionSetComposerEnabled(false);
