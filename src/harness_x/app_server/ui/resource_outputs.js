"use strict";

const projectResourceOutputPatterns = {
  project: /^project_[0-9a-f]{32}$/,
  chat: /^chat_[0-9a-f]{32}$/,
  execution: /^exec_[0-9a-f]{32}$/,
  artifact: /^artifact_[0-9a-f]{32}$/,
  sha256: /^[0-9a-f]{64}$/,
};
let projectResourceOutputGeneration = 0;

function projectResourceExecutionPath(projectId, chatId, executionId, suffix) {
  if (!projectResourceOutputPatterns.project.test(String(projectId || ""))
      || !projectResourceOutputPatterns.chat.test(String(chatId || ""))
      || !projectResourceOutputPatterns.execution.test(String(executionId || ""))
      || !new Set(["diff", "artifacts"]).has(suffix)) {
    throw new Error("Execution output identity is invalid.");
  }
  return `/v1/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chatId)}/executions/${encodeURIComponent(executionId)}/${suffix}`;
}

function projectResourceRenderDiff(diff) {
  const root = dailyById("daily-resource-diff");
  root.replaceChildren();
  const note = projectResourceElement("p", "Read-only current workspace view. Execution authorship is not proven; this view is not verification or evidence.");
  note.className = "muted small";
  root.append(note);
  if (!diff || !diff.available) {
    root.append(projectResourceElement("p", `Diff unavailable · ${String(diff && diff.unavailable_reason || "unknown")}`));
    return;
  }
  const summary = projectResourceElement("p", `${diff.detected_files} changed files · +${diff.additions} / -${diff.deletions}${diff.truncated ? " · bounded projection truncated" : ""}`);
  summary.className = "muted small";
  root.append(summary);
  for (const file of diff.files || []) {
    const details = projectResourceElement("details");
    details.append(projectResourceElement("summary", `${String(file.status)} · ${String(file.path)} · +${Number(file.additions || 0)} / -${Number(file.deletions || 0)}`));
    const body = projectResourceElement(file.patch ? "pre" : "p", file.patch ? String(file.patch) : "No bounded textual patch is available for this entry.");
    body.className = file.patch ? "mono small" : "muted small";
    details.append(body);
    root.append(details);
  }
}

function projectResourceRenderArtifacts(page) {
  const root = dailyById("daily-resource-artifacts");
  root.replaceChildren();
  const artifacts = Array.isArray(page && page.artifacts) ? page.artifacts : [];
  if (!artifacts.length) {
    const empty = projectResourceElement("p", "No registered execution artifacts are available yet.");
    empty.className = "muted small";
    root.append(empty);
    return;
  }
  for (const record of artifacts) {
    const row = projectResourceElement("div");
    row.className = "daily-inline-actions";
    const label = projectResourceElement("span", `${String(record.logical_name || record.storage_name || "Artifact")} · ${Number(record.size_bytes || 0)} bytes`);
    label.className = "small";
    const download = projectResourceElement("button", "Download");
    download.type = "button";
    download.className = "secondary compact-button";
    download.addEventListener("click", () => void projectResourceDownloadArtifact(record));
    row.append(label, download);
    root.append(row);
  }
}

async function projectResourceSha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (item) => item.toString(16).padStart(2, "0")).join("");
}

async function projectResourceDownloadArtifact(record) {
  const projectId = dailyState.selectedProjectId;
  const chatId = dailyState.selectedChatId;
  const executionId = projectResourceState.outputExecutionId;
  if (!projectId || !chatId || !executionId || !state.token) return;
  if (!record || record.project_id !== projectId || record.chat_id !== chatId
      || record.execution_id !== executionId
      || !projectResourceOutputPatterns.artifact.test(String(record.artifact_id || ""))
      || !projectResourceOutputPatterns.sha256.test(String(record.sha256 || ""))
      || !Number.isSafeInteger(record.size_bytes) || record.size_bytes < 0 || record.size_bytes > 4 * 1024 * 1024
      || typeof record.storage_name !== "string" || !record.storage_name || /[\\/";]/.test(record.storage_name)) {
    projectResourceSetStatus("Artifact metadata is inconsistent; download refused.");
    return;
  }
  try {
    const response = await fetch(`${projectResourceExecutionPath(projectId, chatId, executionId, "artifacts")}/${encodeURIComponent(record.artifact_id)}`, {
      method: "GET",
      headers: { Authorization: `Bearer ${state.token}`, Accept: String(record.media_type || "application/octet-stream") },
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
    });
    if (!projectResourceCurrent(projectId, chatId) || projectResourceState.outputExecutionId !== executionId) return;
    if (!response.ok) throw new Error(`artifact HTTP ${response.status}`);
    if (response.headers.get("Content-Length") !== String(record.size_bytes)) throw new Error("artifact content length mismatch");
    if ((response.headers.get("X-Harness-X-Artifact-SHA256") || "") !== record.sha256) throw new Error("artifact digest header mismatch");
    if ((response.headers.get("Content-Disposition") || "") !== `attachment; filename="${record.storage_name}"`) throw new Error("artifact filename header mismatch");
    const bytes = await response.arrayBuffer();
    if (bytes.byteLength !== record.size_bytes) throw new Error("artifact byte count mismatch");
    if (await projectResourceSha256Hex(bytes) !== record.sha256) throw new Error("artifact bytes failed SHA-256 validation");
    const objectUrl = URL.createObjectURL(new Blob([bytes], { type: String(record.media_type || "application/octet-stream") }));
    try {
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = record.storage_name;
      document.body.append(link);
      link.click();
      link.remove();
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
    projectResourceSetStatus(`Downloaded ${record.storage_name} · sha256 ${record.sha256}`);
  } catch (error) {
    projectResourceSetStatus(`Artifact download failed: ${dailyMessage(error)}`);
  }
}

async function projectResourceRefreshOutputs(executionId = projectResourceState.outputExecutionId) {
  const projectId = dailyState.selectedProjectId;
  const chatId = dailyState.selectedChatId;
  if (!projectId || !chatId || !executionId || !projectResourceCurrent(projectId, chatId)) return;
  const generation = ++projectResourceOutputGeneration;
  projectResourceState.outputExecutionId = executionId;
  dailyById("daily-resource-outputs").classList.remove("hidden");
  dailyById("daily-resource-output-id").textContent = executionId;
  try {
    const [diff, artifacts] = await Promise.all([
      api(projectResourceExecutionPath(projectId, chatId, executionId, "diff")),
      api(projectResourceExecutionPath(projectId, chatId, executionId, "artifacts")),
    ]);
    if (generation !== projectResourceOutputGeneration || !projectResourceCurrent(projectId, chatId)
        || projectResourceState.outputExecutionId !== executionId) return;
    if (diff.execution_id !== executionId || artifacts.execution_id !== executionId) throw new Error("Execution output identity changed during refresh.");
    projectResourceRenderDiff(diff);
    projectResourceRenderArtifacts(artifacts);
  } catch (error) {
    if (generation !== projectResourceOutputGeneration) return;
    dailyById("daily-resource-diff").replaceChildren(projectResourceElement("p", `Diff unavailable: ${dailyMessage(error)}`));
    dailyById("daily-resource-artifacts").replaceChildren(projectResourceElement("p", "Artifact list unavailable."));
  }
}

function projectResourceSetOutputExecution(executionId) {
  projectResourceOutputGeneration += 1;
  projectResourceState.outputExecutionId = executionId;
  const panel = dailyById("daily-resource-outputs");
  if (!executionId) {
    panel.classList.add("hidden");
    dailyById("daily-resource-output-id").textContent = "";
    dailyById("daily-resource-diff").replaceChildren();
    dailyById("daily-resource-artifacts").replaceChildren();
    return;
  }
  void projectResourceRefreshOutputs(executionId);
}

function projectResourceBuildOutputsUi() {
  const outputs = projectResourceElement("section");
  outputs.id = "daily-resource-outputs";
  outputs.className = "daily-inline-form hidden";
  const heading = projectResourceElement("div");
  heading.className = "daily-inline-actions";
  const id = projectResourceElement("span");
  id.id = "daily-resource-output-id";
  id.className = "mono muted small";
  const refresh = projectResourceElement("button", "Refresh");
  refresh.type = "button";
  refresh.className = "secondary compact-button";
  refresh.addEventListener("click", () => void projectResourceRefreshOutputs());
  heading.append(projectResourceElement("strong", "Execution outputs"), id, refresh);
  const diff = projectResourceElement("div");
  diff.id = "daily-resource-diff";
  const artifacts = projectResourceElement("div");
  artifacts.id = "daily-resource-artifacts";
  outputs.append(heading, projectResourceElement("p", "Workspace changes"), diff, projectResourceElement("p", "Registered artifacts"), artifacts);
  const composer = document.querySelector(".daily-composer-wrap");
  if (!composer || !composer.parentNode) throw new Error("daily composer wrapper is unavailable");
  composer.parentNode.insertBefore(outputs, composer);
}

projectResourceBuildOutputsUi();
const projectResourceResetActivityBefore = conversationExecutionResetActivity;
conversationExecutionResetActivity = function conversationExecutionResetActivityWithResources(executionId = null) {
  projectResourceResetActivityBefore(executionId);
  projectResourceSetOutputExecution(executionId);
};
const projectResourceMergeActivityBefore = conversationExecutionMergeActivity;
conversationExecutionMergeActivity = function conversationExecutionMergeActivityWithResources(page) {
  projectResourceMergeActivityBefore(page);
  if (page && page.execution_id && page.terminal && !page.has_more) void projectResourceRefreshOutputs(page.execution_id);
};
const projectResourceOutputSelectChatBefore = dailySelectChat;
dailySelectChat = async function dailySelectChatWithResourceOutputs(chatId, options) {
  projectResourceSetOutputExecution(null);
  await projectResourceOutputSelectChatBefore(chatId, options);
};
const projectResourceOutputClearChatBefore = dailyClearChatSelection;
dailyClearChatSelection = function dailyClearChatSelectionWithResourceOutputs() {
  projectResourceSetOutputExecution(null);
  projectResourceOutputClearChatBefore();
};
dailyById("lock-button").addEventListener("click", () => projectResourceSetOutputExecution(null));
