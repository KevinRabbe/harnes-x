"use strict";

// M72 is presentation-only over durable approval state. The browser never authors action data.
const sensitiveApprovalState = {
  pollTimer: null,
  pollGeneration: 0,
  executionId: null,
  approvals: new Map(),
};

function sensitiveApprovalEnsureRegion() {
  let region = document.getElementById("daily-sensitive-approvals");
  if (region) return region;

  region = document.createElement("section");
  region.id = "daily-sensitive-approvals";
  region.className = "daily-inline-form hidden";
  region.setAttribute("aria-live", "polite");

  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "Sensitive action approval";
  region.appendChild(eyebrow);

  const list = document.createElement("div");
  list.id = "daily-sensitive-approval-list";
  region.appendChild(list);

  const activity = document.getElementById("daily-work-activity");
  const composerWrap = document.querySelector(".daily-composer-wrap");
  const anchor = activity || composerWrap;
  if (!anchor || !anchor.parentNode) throw new Error("daily approval insertion point is unavailable");
  anchor.parentNode.insertBefore(region, composerWrap || anchor.nextSibling);
  return region;
}

function sensitiveApprovalPath(projectId, chatId, executionId, approvalId = null) {
  const base = `${conversationExecutionPath(projectId, chatId, executionId)}/approvals`;
  return approvalId == null ? base : `${base}/${encodeURIComponent(approvalId)}`;
}

function sensitiveApprovalStopPolling() {
  sensitiveApprovalState.pollGeneration += 1;
  if (sensitiveApprovalState.pollTimer != null) {
    clearTimeout(sensitiveApprovalState.pollTimer);
    sensitiveApprovalState.pollTimer = null;
  }
}

function sensitiveApprovalReset(executionId = null) {
  sensitiveApprovalStopPolling();
  sensitiveApprovalState.executionId = executionId;
  sensitiveApprovalState.approvals = new Map();
  sensitiveApprovalRender();
}

function sensitiveApprovalRenderDetails(container, details) {
  const entries = Object.entries(details || {});
  if (!entries.length) return;
  const detail = document.createElement("p");
  detail.className = "mono muted small";
  detail.textContent = entries.map(([key, value]) => `${key}: ${String(value)}`).join(" · ");
  container.appendChild(detail);
}

async function sensitiveApprovalDecide(projectId, chatId, executionId, approvalId, decision) {
  const projection = await api(
    sensitiveApprovalPath(projectId, chatId, executionId, approvalId),
    {
      method: "POST",
      body: JSON.stringify({
        schema_version: "sensitive-action-approval-decision-request-v1",
        decision,
      }),
    },
  );
  if (projection.approval_id !== approvalId) throw new Error("approval identity mismatch");
  sensitiveApprovalState.approvals.set(approvalId, projection);
  sensitiveApprovalRender();
}

function sensitiveApprovalRender() {
  const region = sensitiveApprovalEnsureRegion();
  const list = dailyById("daily-sensitive-approval-list");
  list.replaceChildren();

  const approvals = [...sensitiveApprovalState.approvals.values()];
  if (!sensitiveApprovalState.executionId || !approvals.length) {
    region.classList.add("hidden");
    return;
  }
  region.classList.remove("hidden");

  for (const item of approvals) {
    const card = document.createElement("div");
    card.className = "daily-inline-form";
    card.dataset.approvalId = String(item.approval_id || "");

    const title = document.createElement("p");
    title.className = "small";
    title.textContent = String(item.summary || item.tool_name || "Sensitive action");
    card.appendChild(title);

    const status = document.createElement("p");
    status.className = "mono muted small";
    status.textContent = `Status: ${String(item.status || "pending")} · ${String(item.approval_id || "approval")}`;
    card.appendChild(status);
    sensitiveApprovalRenderDetails(card, item.details);

    if (item.status === "pending") {
      const controls = document.createElement("div");
      controls.className = "button-row";
      const approve = document.createElement("button");
      approve.type = "button";
      approve.textContent = "Approve";
      const reject = document.createElement("button");
      reject.type = "button";
      reject.className = "secondary";
      reject.textContent = "Reject";

      const projectId = dailyState.selectedProjectId;
      const chatId = dailyState.selectedChatId;
      const executionId = sensitiveApprovalState.executionId;
      const approvalId = String(item.approval_id || "");
      const decide = async (decision) => {
        if (!projectId || !chatId || !executionId || !approvalId) return;
        approve.disabled = true;
        reject.disabled = true;
        try {
          await sensitiveApprovalDecide(projectId, chatId, executionId, approvalId, decision);
        } catch (error) {
          dailyById("daily-composer-status").textContent = `Approval decision failed: ${dailyMessage(error)}`;
          approve.disabled = false;
          reject.disabled = false;
        }
      };
      approve.addEventListener("click", () => void decide("approve"));
      reject.addEventListener("click", () => void decide("reject"));
      controls.append(approve, reject);
      card.appendChild(controls);
    }
    list.appendChild(card);
  }
}

function sensitiveApprovalSchedule(projectId, chatId, executionId, generation, delayMs = 650) {
  if (generation !== sensitiveApprovalState.pollGeneration) return;
  if (!conversationExecutionCurrent(projectId, chatId)) return;
  if (sensitiveApprovalState.executionId !== executionId) return;
  if (sensitiveApprovalState.pollTimer != null) clearTimeout(sensitiveApprovalState.pollTimer);
  sensitiveApprovalState.pollTimer = setTimeout(() => {
    sensitiveApprovalState.pollTimer = null;
    void sensitiveApprovalRefresh(projectId, chatId, executionId, generation);
  }, delayMs);
}

async function sensitiveApprovalRefresh(projectId, chatId, executionId, generation) {
  if (generation !== sensitiveApprovalState.pollGeneration) return;
  if (!conversationExecutionCurrent(projectId, chatId)) return;
  try {
    const page = await api(sensitiveApprovalPath(projectId, chatId, executionId));
    if (generation !== sensitiveApprovalState.pollGeneration) return;
    if (!conversationExecutionCurrent(projectId, chatId)) return;
    if (page.execution_id !== executionId) throw new Error("approval execution identity mismatch");
    const next = new Map();
    for (const item of page.approvals || []) {
      const approvalId = String(item.approval_id || "");
      if (approvalId) next.set(approvalId, item);
    }
    sensitiveApprovalState.approvals = next;
    sensitiveApprovalRender();
    const unresolved = [...next.values()].some(
      (item) => item.status === "pending" || item.status === "approved",
    );
    if (conversationExecutionState.activeExecution || unresolved) {
      sensitiveApprovalSchedule(projectId, chatId, executionId, generation);
    }
  } catch (error) {
    if (generation !== sensitiveApprovalState.pollGeneration) return;
    if (!conversationExecutionCurrent(projectId, chatId)) return;
    dailyById("daily-composer-status").textContent = `Approval state unavailable: ${dailyMessage(error)} · retrying locally`;
    sensitiveApprovalSchedule(projectId, chatId, executionId, generation, 1200);
  }
}

const conversationExecutionResetActivityBeforeSensitiveApproval = conversationExecutionResetActivity;
conversationExecutionResetActivity = function conversationExecutionResetActivityWithSensitiveApproval(executionId = null) {
  conversationExecutionResetActivityBeforeSensitiveApproval(executionId);
  sensitiveApprovalReset(executionId);
  const projectId = dailyState.selectedProjectId;
  const chatId = dailyState.selectedChatId;
  if (executionId && projectId && chatId && conversationExecutionCurrent(projectId, chatId)) {
    const generation = sensitiveApprovalState.pollGeneration;
    sensitiveApprovalSchedule(projectId, chatId, executionId, generation, 0);
  }
};

const conversationExecutionStopPollingBeforeSensitiveApproval = conversationExecutionStopPolling;
conversationExecutionStopPolling = function conversationExecutionStopPollingWithSensitiveApproval() {
  sensitiveApprovalStopPolling();
  conversationExecutionStopPollingBeforeSensitiveApproval();
};

sensitiveApprovalEnsureRegion();
