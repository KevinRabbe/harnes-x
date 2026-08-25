"use strict";

// M69 execution wrapper. The M68 workspace remains the durable Project/Chat projection; this
// script replaces only composer submission and adds coarse execution restoration/polling. It
// reuses the inherited authenticated `api` binding and never reads or exports the bearer token.
const conversationExecutionState = {
  pollTimer: null,
  pollGeneration: 0,
  activeExecution: null,
  pendingSubmission: null,
};

function conversationExecutionSubmissionId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  const suffix = [...bytes]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
  return `submission_${suffix}`;
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

function conversationExecutionSchedulePoll(projectId, chatId, executionId, generation) {
  if (generation !== conversationExecutionState.pollGeneration) return;
  if (!conversationExecutionCurrent(projectId, chatId)) return;
  if (conversationExecutionState.pollTimer != null) clearTimeout(conversationExecutionState.pollTimer);
  conversationExecutionState.pollTimer = setTimeout(() => {
    conversationExecutionState.pollTimer = null;
    void conversationExecutionPoll(projectId, chatId, executionId, generation);
  }, 1000);
}

async function conversationExecutionPoll(projectId, chatId, executionId, generation) {
  if (generation !== conversationExecutionState.pollGeneration) return;
  if (!conversationExecutionCurrent(projectId, chatId)) return;
  try {
    const projection = await api(conversationExecutionPath(projectId, chatId, executionId));
    if (generation !== conversationExecutionState.pollGeneration) return;
    if (!conversationExecutionCurrent(projectId, chatId)) return;
    dailyById("daily-composer-status").textContent = conversationExecutionStatusText(projection);
    if (projection.terminal) {
      conversationExecutionState.activeExecution = null;
      conversationExecutionSetComposerEnabled(true);
      await conversationExecutionRefreshTerminal(projectId, chatId);
      return;
    }
    conversationExecutionState.activeExecution = projection;
    conversationExecutionSetComposerEnabled(false);
    conversationExecutionSchedulePoll(projectId, chatId, executionId, generation);
  } catch (error) {
    if (generation !== conversationExecutionState.pollGeneration) return;
    if (!conversationExecutionCurrent(projectId, chatId)) return;
    dailyById("daily-composer-status").textContent = (
      `Execution status unavailable: ${dailyMessage(error)} · retrying locally`
    );
    conversationExecutionSchedulePoll(projectId, chatId, executionId, generation);
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
      conversationExecutionState.activeExecution = null;
      conversationExecutionSetComposerEnabled(true);
      dailyById("daily-composer-status").textContent = "Ready for a Harness X work turn.";
      return;
    }
    dailyById("daily-composer-status").textContent = conversationExecutionStatusText(latest);
    if (latest.terminal) {
      conversationExecutionState.activeExecution = null;
      conversationExecutionSetComposerEnabled(true);
      await conversationExecutionRefreshTerminal(projectId, chatId);
      return;
    }
    conversationExecutionState.activeExecution = latest;
    conversationExecutionSetComposerEnabled(false);
    const generation = conversationExecutionState.pollGeneration;
    conversationExecutionSchedulePoll(projectId, chatId, latest.execution_id, generation);
  } catch (error) {
    if (!conversationExecutionCurrent(projectId, chatId)) return;
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
  dailyClearChatSelectionBeforeConversationExecution();
  conversationExecutionSetComposerEnabled(false);
};

dailyById("daily-composer").addEventListener("submit", async (event) => {
  // Capture-phase interception prevents the frozen M68 local-message submit listener from
  // running for M69 work turns.
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
    dailyById("daily-composer-status").textContent = conversationExecutionStatusText(projection);
    if (projection.terminal) {
      conversationExecutionState.activeExecution = null;
      conversationExecutionSetComposerEnabled(true);
      await conversationExecutionRefreshTerminal(projectId, chatId);
      return;
    }
    conversationExecutionState.activeExecution = projection;
    const generation = conversationExecutionState.pollGeneration;
    conversationExecutionSchedulePoll(projectId, chatId, projection.execution_id, generation);
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
  conversationExecutionSetComposerEnabled(false);
});

if (!dailyState.selectedChatId) conversationExecutionSetComposerEnabled(false);
