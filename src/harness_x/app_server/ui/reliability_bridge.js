"use strict";

// M75 everyday reliability overlay. Durable Project/Chat/execution/session records remain the
// authority. This bridge keeps only transient page-memory retry state and never stores a bearer,
// task body, activity cursor, approval, resource bytes, or evidence in browser persistence.
const everydayReliabilityState = {
  activityFailures: 0,
  activityExhausted: false,
  lastActivityError: null,
  retryPending: null,
  latest: null,
  generation: 0,
};
const EVERYDAY_ACTIVITY_RETRY_DELAYS_MS = Object.freeze([250, 500, 1000, 2000, 4000]);
const everydayReliabilityPatterns = {
  project: /^project_[0-9a-f]{32}$/,
  chat: /^chat_[0-9a-f]{32}$/,
  execution: /^exec_[0-9a-f]{32}$/,
  submission: /^submission_[0-9a-f]{32}$/,
  executionCollection: /^\/v1\/projects\/(project_[0-9a-f]{32})\/chats\/(chat_[0-9a-f]{32})\/executions$/,
  activity: /^\/v1\/projects\/(project_[0-9a-f]{32})\/chats\/(chat_[0-9a-f]{32})\/executions\/(exec_[0-9a-f]{32})\/activity\?cursor=[^&]+&limit=100$/,
};

function everydayReliabilityElement(tag, text = "") {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  return node;
}

function everydayReliabilityExecutionPath(projectId, chatId, executionId, suffix) {
  if (!everydayReliabilityPatterns.project.test(String(projectId || ""))
      || !everydayReliabilityPatterns.chat.test(String(chatId || ""))
      || !everydayReliabilityPatterns.execution.test(String(executionId || ""))
      || !new Set(["reliability", "stop", "retry"]).has(suffix)) {
    throw new Error("Everyday reliability identity is invalid.");
  }
  return `/v1/projects/${projectId}/chats/${chatId}/executions/${executionId}/${suffix}`;
}

function everydayReliabilityCurrent(projectId, chatId, executionId = null) {
  if (!dailyState.unlocked
      || dailyState.selectedProjectId !== projectId
      || dailyState.selectedChatId !== chatId) return false;
  return executionId == null || conversationExecutionState.activityExecutionId === executionId;
}

function everydayReliabilityEnsureUi() {
  let region = document.getElementById("daily-reliability");
  if (region) return region;
  region = everydayReliabilityElement("section");
  region.id = "daily-reliability";
  region.className = "daily-inline-form hidden";
  region.setAttribute("aria-live", "polite");

  const status = everydayReliabilityElement("p");
  status.id = "daily-reliability-status";
  status.className = "muted small";

  const actions = everydayReliabilityElement("div");
  actions.className = "daily-inline-actions";

  const reconnect = everydayReliabilityElement("button", "Reconnect activity");
  reconnect.id = "daily-reliability-reconnect";
  reconnect.type = "button";
  reconnect.className = "secondary compact-button hidden";
  reconnect.addEventListener("click", everydayReliabilityReconnectActivity);

  const stop = everydayReliabilityElement("button", "Stop");
  stop.id = "daily-reliability-stop";
  stop.type = "button";
  stop.className = "danger compact-button hidden";
  stop.addEventListener("click", () => void everydayReliabilityStop());

  const retry = everydayReliabilityElement("button", "Retry");
  retry.id = "daily-reliability-retry";
  retry.type = "button";
  retry.className = "secondary compact-button hidden";
  retry.addEventListener("click", () => void everydayReliabilityRetry());

  const continueButton = everydayReliabilityElement("button", "Continue");
  continueButton.id = "daily-reliability-continue";
  continueButton.type = "button";
  continueButton.className = "secondary compact-button hidden";
  continueButton.addEventListener("click", everydayReliabilityContinue);

  actions.append(reconnect, stop, retry, continueButton);
  region.append(status, actions);
  const composer = document.querySelector(".daily-composer-wrap");
  if (!composer || !composer.parentNode) throw new Error("daily composer wrapper is unavailable");
  composer.parentNode.insertBefore(region, composer);
  return region;
}

function everydayReliabilityRender() {
  const region = everydayReliabilityEnsureUi();
  const latest = everydayReliabilityState.latest;
  const reconnect = dailyById("daily-reliability-reconnect");
  const stop = dailyById("daily-reliability-stop");
  const retry = dailyById("daily-reliability-retry");
  const continueButton = dailyById("daily-reliability-continue");
  const status = dailyById("daily-reliability-status");
  const executionId = conversationExecutionState.activityExecutionId;

  reconnect.classList.toggle("hidden", !everydayReliabilityState.activityExhausted);
  reconnect.disabled = !everydayReliabilityState.activityExhausted || !executionId;
  stop.classList.toggle("hidden", !latest || !latest.can_stop);
  stop.disabled = !latest || !latest.can_stop;
  retry.classList.toggle("hidden", !latest || !latest.can_retry);
  retry.disabled = !latest || !latest.can_retry;
  continueButton.classList.toggle("hidden", !latest || !latest.can_continue);
  continueButton.disabled = !latest || !latest.can_continue;

  if (!executionId && !everydayReliabilityState.activityExhausted) {
    region.classList.add("hidden");
    status.textContent = "";
    return;
  }
  region.classList.remove("hidden");
  if (latest && latest.interrupted_by_restart) {
    status.textContent = (
      "Interrupted by an App Server restart. The prior model/tool process was not resumed; "
      + "Retry starts a new execution and Continue starts a new user-authored turn."
    );
    return;
  }
  if (everydayReliabilityState.activityExhausted) {
    status.textContent = (
      "Live activity reconnect paused after bounded transport retries. "
      + "Server-owned execution state was not changed."
    );
    return;
  }
  if (latest && latest.retry_source_execution_id) {
    status.textContent = `This execution is an explicit retry of ${latest.retry_source_execution_id}.`;
    return;
  }
  status.textContent = "";
}

function everydayReliabilityResetTransient({ clearRetry = false } = {}) {
  everydayReliabilityState.generation += 1;
  everydayReliabilityState.activityFailures = 0;
  everydayReliabilityState.activityExhausted = false;
  everydayReliabilityState.lastActivityError = null;
  everydayReliabilityState.latest = null;
  if (clearRetry) everydayReliabilityState.retryPending = null;
  everydayReliabilityRender();
}

function everydayReliabilityActivityFailure(error) {
  everydayReliabilityState.lastActivityError = error;
}

function everydayReliabilityActivityValidated() {
  everydayReliabilityState.lastActivityError = null;
  everydayReliabilityState.activityFailures = 0;
  everydayReliabilityState.activityExhausted = false;
  everydayReliabilityRender();
}

function everydayReliabilityIsRetriableActivityError(error) {
  return !(error && typeof error.status === "number" && error.status < 500);
}

function everydayReliabilityPauseActivity(message) {
  everydayReliabilityState.activityExhausted = true;
  const status = dailyById("daily-composer-status");
  if (status) status.textContent = message;
  everydayReliabilityRender();
}

function everydayReliabilityReconnectActivity() {
  const projectId = dailyState.selectedProjectId;
  const chatId = dailyState.selectedChatId;
  const executionId = conversationExecutionState.activityExecutionId;
  if (!projectId || !chatId || !executionId) return;
  if (!everydayReliabilityCurrent(projectId, chatId, executionId)) return;
  everydayReliabilityState.activityFailures = 0;
  everydayReliabilityState.activityExhausted = false;
  everydayReliabilityState.lastActivityError = null;
  dailyById("daily-composer-status").textContent = "Reconnecting to server-owned execution activity…";
  everydayReliabilityRender();
  conversationExecutionSchedulePoll(
    projectId,
    chatId,
    executionId,
    conversationExecutionState.pollGeneration,
    0,
  );
}

async function everydayReliabilityRefresh(projectId, chatId, executionId) {
  if (!everydayReliabilityCurrent(projectId, chatId, executionId)) return null;
  const generation = everydayReliabilityState.generation;
  try {
    const projection = await api(
      everydayReliabilityExecutionPath(projectId, chatId, executionId, "reliability"),
    );
    if (generation !== everydayReliabilityState.generation) return null;
    if (!everydayReliabilityCurrent(projectId, chatId, executionId)) return null;
    if (!projection
        || projection.execution_id !== executionId
        || projection.project_id !== projectId
        || projection.chat_id !== chatId
        || typeof projection.terminal !== "boolean"
        || typeof projection.can_stop !== "boolean"
        || typeof projection.can_retry !== "boolean"
        || typeof projection.can_continue !== "boolean") {
      throw new Error("Everyday reliability projection identity is inconsistent.");
    }
    everydayReliabilityState.latest = projection;
    everydayReliabilityRender();
    return projection;
  } catch (error) {
    if (generation !== everydayReliabilityState.generation) return null;
    if (!everydayReliabilityCurrent(projectId, chatId, executionId)) return null;
    everydayReliabilityState.latest = null;
    everydayReliabilityRender();
    dailyById("daily-composer-status").textContent = (
      `Recovery controls unavailable: ${dailyMessage(error)}`
    );
    return null;
  }
}

async function everydayReliabilityStop() {
  const projectId = dailyState.selectedProjectId;
  const chatId = dailyState.selectedChatId;
  const latest = everydayReliabilityState.latest;
  if (!projectId || !chatId || !latest || !latest.can_stop) return;
  const executionId = latest.execution_id;
  if (!everydayReliabilityCurrent(projectId, chatId, executionId)) return;
  const button = dailyById("daily-reliability-stop");
  button.disabled = true;
  dailyById("daily-composer-status").textContent = "Requesting stop for the current execution…";
  try {
    const projection = await api(
      everydayReliabilityExecutionPath(projectId, chatId, executionId, "stop"),
      {
        method: "POST",
        body: JSON.stringify({ schema_version: "conversation-execution-stop-v1" }),
      },
    );
    if (!everydayReliabilityCurrent(projectId, chatId, executionId)) return;
    everydayReliabilityState.latest = projection;
    dailyById("daily-composer-status").textContent = (
      projection.terminal
        ? conversationExecutionStatusText(projection)
        : `Stop requested · ${executionId}`
    );
    everydayReliabilityRender();
  } catch (error) {
    if (!everydayReliabilityCurrent(projectId, chatId, executionId)) return;
    dailyById("daily-composer-status").textContent = `Stop request failed: ${dailyMessage(error)}`;
    everydayReliabilityRender();
  }
}

function everydayReliabilityRetryPending(projectId, chatId, executionId) {
  let pending = everydayReliabilityState.retryPending;
  if (!pending
      || pending.projectId !== projectId
      || pending.chatId !== chatId
      || pending.sourceExecutionId !== executionId) {
    pending = {
      projectId,
      chatId,
      sourceExecutionId: executionId,
      submissionId: conversationExecutionSubmissionId(),
    };
    everydayReliabilityState.retryPending = pending;
  }
  return pending;
}

async function everydayReliabilityRetry() {
  const projectId = dailyState.selectedProjectId;
  const chatId = dailyState.selectedChatId;
  const latest = everydayReliabilityState.latest;
  if (!projectId || !chatId || !latest || !latest.can_retry) return;
  const sourceExecutionId = latest.execution_id;
  if (!everydayReliabilityCurrent(projectId, chatId, sourceExecutionId)) return;
  const pending = everydayReliabilityRetryPending(projectId, chatId, sourceExecutionId);
  const button = dailyById("daily-reliability-retry");
  button.disabled = true;
  dailyById("daily-composer-status").textContent = "Starting an explicit retry from frozen execution inputs…";
  try {
    const result = await api(
      everydayReliabilityExecutionPath(projectId, chatId, sourceExecutionId, "retry"),
      {
        method: "POST",
        body: JSON.stringify({
          schema_version: "conversation-execution-retry-v1",
          submission_id: pending.submissionId,
        }),
      },
    );
    const projection = result && result.execution;
    if (!projection
        || result.source_execution_id !== sourceExecutionId
        || projection.project_id !== projectId
        || projection.chat_id !== chatId
        || !everydayReliabilityPatterns.execution.test(String(projection.execution_id || ""))) {
      throw new Error("Retry response identity is inconsistent.");
    }
    everydayReliabilityState.retryPending = null;
    if (!conversationExecutionCurrent(projectId, chatId)) return;
    conversationExecutionStopPolling();
    conversationExecutionResetActivity(projection.execution_id);
    conversationExecutionState.activeExecution = projection.terminal ? null : projection;
    conversationExecutionSetComposerEnabled(Boolean(projection.terminal));
    dailyById("daily-composer-status").textContent = conversationExecutionStatusText(projection);
    await dailyLoadMessages(chatId);
    if (!conversationExecutionCurrent(projectId, chatId)) return;
    conversationExecutionSchedulePoll(
      projectId,
      chatId,
      projection.execution_id,
      conversationExecutionState.pollGeneration,
      0,
    );
  } catch (error) {
    if (error && typeof error.status === "number" && error.status < 500) {
      everydayReliabilityState.retryPending = null;
    }
    if (!conversationExecutionCurrent(projectId, chatId)) return;
    const note = everydayReliabilityState.retryPending
      ? " Retry will reuse the same retry submission identity."
      : "";
    dailyById("daily-composer-status").textContent = (
      `Retry failed: ${dailyMessage(error)}.${note}`
    );
    everydayReliabilityRender();
  }
}

function everydayReliabilityContinue() {
  const latest = everydayReliabilityState.latest;
  if (!latest || !latest.can_continue || !latest.terminal) return;
  const text = dailyById("daily-composer-text");
  if (text.disabled) return;
  dailyById("daily-composer-status").textContent = (
    latest.interrupted_by_restart
      ? "Write a new turn from the durable chat state; the interrupted runtime is not resumed."
      : "Write the next Harness X turn."
  );
  text.focus();
}

function everydayReliabilityReconcileExecutionList(path, page) {
  const match = everydayReliabilityPatterns.executionCollection.exec(path);
  if (!match || !page || !Array.isArray(page.executions)) return;
  const [, projectId, chatId] = match;
  const executions = page.executions;
  const pending = conversationExecutionState.pendingSubmission;
  if (pending
      && pending.projectId === projectId
      && pending.chatId === chatId
      && executions.some((item) => item.submission_id === pending.submissionId)) {
    if (dailyState.selectedProjectId === projectId
        && dailyState.selectedChatId === chatId
        && dailyById("daily-composer-text").value === pending.text) {
      dailyById("daily-composer-text").value = "";
    }
    if (typeof projectResourceState !== "undefined") {
      projectResourceState.submissionResources.delete(pending.submissionId);
    }
    conversationExecutionState.pendingSubmission = null;
  }
  const retry = everydayReliabilityState.retryPending;
  if (retry
      && retry.projectId === projectId
      && retry.chatId === chatId
      && executions.some((item) => item.submission_id === retry.submissionId)) {
    everydayReliabilityState.retryPending = null;
  }
}

const everydayReliabilityApiBefore = api;
api = async function apiWithEverydayReliability(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const activityMatch = method === "GET" ? everydayReliabilityPatterns.activity.exec(path) : null;
  try {
    const result = await everydayReliabilityApiBefore(path, options);
    if (activityMatch) everydayReliabilityActivityValidated();
    if (method === "GET") everydayReliabilityReconcileExecutionList(path, result);
    return result;
  } catch (error) {
    if (activityMatch) everydayReliabilityActivityFailure(error);
    throw error;
  }
};

const everydayReliabilitySchedulePollBefore = conversationExecutionSchedulePoll;
conversationExecutionSchedulePoll = function conversationExecutionSchedulePollWithBoundedRecovery(
  projectId,
  chatId,
  executionId,
  generation,
  delayMs = 850,
) {
  if (delayMs !== 1200) {
    if (delayMs !== 0) everydayReliabilityActivityValidated();
    const result = everydayReliabilitySchedulePollBefore(
      projectId,
      chatId,
      executionId,
      generation,
      delayMs,
    );
    if (delayMs === 0 && everydayReliabilityCurrent(projectId, chatId, executionId)) {
      void everydayReliabilityRefresh(projectId, chatId, executionId);
    }
    return result;
  }

  const error = everydayReliabilityState.lastActivityError;
  if (!everydayReliabilityIsRetriableActivityError(error)) {
    everydayReliabilityPauseActivity(
      `Execution activity stopped: ${dailyMessage(error)}. Re-select the chat after resolving the server response.`,
    );
    return undefined;
  }
  const retryIndex = everydayReliabilityState.activityFailures;
  if (retryIndex >= EVERYDAY_ACTIVITY_RETRY_DELAYS_MS.length) {
    everydayReliabilityPauseActivity(
      "Execution activity unavailable after bounded reconnect attempts. Use Reconnect activity to try again.",
    );
    return undefined;
  }
  const boundedDelay = EVERYDAY_ACTIVITY_RETRY_DELAYS_MS[retryIndex];
  everydayReliabilityState.activityFailures += 1;
  everydayReliabilityState.activityExhausted = false;
  dailyById("daily-composer-status").textContent = (
    `Execution activity unavailable: ${dailyMessage(error)} · reconnect ${everydayReliabilityState.activityFailures}`
    + `/${EVERYDAY_ACTIVITY_RETRY_DELAYS_MS.length}`
  );
  everydayReliabilityRender();
  return everydayReliabilitySchedulePollBefore(
    projectId,
    chatId,
    executionId,
    generation,
    boundedDelay,
  );
};

const everydayReliabilityRefreshTerminalBefore = conversationExecutionRefreshTerminal;
conversationExecutionRefreshTerminal = async function conversationExecutionRefreshTerminalWithReliability(
  projectId,
  chatId,
) {
  await everydayReliabilityRefreshTerminalBefore(projectId, chatId);
  const executionId = conversationExecutionState.activityExecutionId;
  if (executionId && everydayReliabilityCurrent(projectId, chatId, executionId)) {
    await everydayReliabilityRefresh(projectId, chatId, executionId);
  }
};

const everydayReliabilitySelectChatBefore = dailySelectChat;
dailySelectChat = async function dailySelectChatWithEverydayReliability(chatId, options) {
  const changesChat = dailyState.selectedChatId !== chatId;
  if (changesChat) everydayReliabilityResetTransient({ clearRetry: true });
  await everydayReliabilitySelectChatBefore(chatId, options);
  const projectId = dailyState.selectedProjectId;
  const executionId = conversationExecutionState.activityExecutionId;
  if (projectId && dailyState.selectedChatId === chatId && executionId) {
    await everydayReliabilityRefresh(projectId, chatId, executionId);
  }
};

const everydayReliabilityClearChatBefore = dailyClearChatSelection;
dailyClearChatSelection = function dailyClearChatSelectionWithEverydayReliability() {
  everydayReliabilityResetTransient({ clearRetry: true });
  everydayReliabilityClearChatBefore();
};

document.addEventListener("keydown", (event) => {
  if (event.defaultPrevented || event.isComposing) return;
  const target = event.target;
  const editable = target instanceof HTMLInputElement
    || target instanceof HTMLTextAreaElement
    || (target instanceof HTMLElement && target.isContentEditable);
  const command = event.ctrlKey || event.metaKey;
  if (command && !event.altKey && !event.shiftKey && event.key === "Enter") {
    if (target !== dailyById("daily-composer-text")) return;
    const send = dailyById("daily-send-message");
    if (send.disabled || dailyById("daily-composer-text").disabled) return;
    event.preventDefault();
    dailyById("daily-composer").requestSubmit();
    return;
  }
  if (editable) return;
  if (command && event.shiftKey && !event.altKey && event.key.toLowerCase() === "m") {
    const text = dailyById("daily-composer-text");
    if (!text.disabled && dailyState.selectedChatId) {
      event.preventDefault();
      text.focus();
    }
    return;
  }
  if (command && event.shiftKey && !event.altKey && event.key.toLowerCase() === "n") {
    const button = dailyById("daily-new-chat-button");
    if (dailyState.unlocked && dailyState.selectedProjectId && !button.disabled) {
      event.preventDefault();
      button.click();
    }
  }
});

dailyById("lock-button").addEventListener("click", () => {
  everydayReliabilityResetTransient({ clearRetry: true });
});

everydayReliabilityEnsureUi();
everydayReliabilityRender();
