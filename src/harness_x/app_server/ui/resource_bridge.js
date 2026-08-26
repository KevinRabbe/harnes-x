"use strict";

const projectResourceState = {
  generation: 0,
  resources: [],
  submissionResources: new Map(),
  outputExecutionId: null,
};
const PROJECT_RESOURCE_MAX_ITEMS = 4;
const PROJECT_RESOURCE_MAX_ATTACHMENT_BYTES = 1024 * 1024;
const projectResourceIdPatterns = {
  attachment: /^attachment_[0-9a-f]{32}$/,
};

function projectResourceElement(tag, text = "") {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  return node;
}

function projectResourceCurrent(projectId, chatId) {
  return dailyState.unlocked
    && dailyState.selectedProjectId === projectId
    && dailyState.selectedChatId === chatId;
}

function projectResourceSetStatus(text) {
  const node = document.getElementById("daily-resource-status");
  if (node) node.textContent = text;
}

function projectResourceReferences() {
  return projectResourceState.resources.map((item) => item.kind === "attachment"
    ? { kind: "attachment", attachment_id: item.attachment_id }
    : { kind: "workspace_file", source_path: item.source_path });
}

function projectResourceSelectionChanged() {
  if (typeof conversationExecutionState !== "undefined") {
    const pending = conversationExecutionState.pendingSubmission;
    if (pending && typeof pending.submissionId === "string") {
      projectResourceState.submissionResources.delete(pending.submissionId);
    }
    conversationExecutionState.pendingSubmission = null;
  }
}

function projectResourceRenderSelection() {
  const root = dailyById("daily-resource-list");
  root.replaceChildren();
  if (!projectResourceState.resources.length) {
    const empty = projectResourceElement("p", "No resources selected for the next work turn.");
    empty.className = "muted small";
    root.append(empty);
    return;
  }
  projectResourceState.resources.forEach((item, index) => {
    const row = projectResourceElement("div");
    row.className = "daily-inline-actions";
    const label = projectResourceElement("span", item.kind === "attachment"
      ? `Attachment · ${item.label} · ${item.size_bytes} bytes`
      : `Workspace file · ${item.source_path}`);
    label.className = "small";
    const remove = projectResourceElement("button", "Remove");
    remove.type = "button";
    remove.className = "secondary compact-button";
    remove.addEventListener("click", () => {
      projectResourceState.resources.splice(index, 1);
      projectResourceSelectionChanged();
      projectResourceSetStatus("");
      projectResourceRenderSelection();
    });
    row.append(label, remove);
    root.append(row);
  });
}

function projectResourceClearSelection() {
  projectResourceState.generation += 1;
  projectResourceState.resources = [];
  const fileInput = document.getElementById("daily-resource-attachments");
  if (fileInput) fileInput.value = "";
  const pathInput = document.getElementById("daily-resource-workspace-path");
  if (pathInput) pathInput.value = "";
  projectResourceSetStatus("");
  projectResourceRenderSelection();
}

function projectResourceNormalizeWorkspacePath(raw) {
  const value = String(raw || "").trim();
  if (!value || value.length > 1024) throw new Error("Workspace reference must be a relative path up to 1024 characters.");
  if (value.includes("\\") || value.startsWith("/") || /^[A-Za-z]:/.test(value)) {
    throw new Error("Workspace reference must use a relative POSIX path.");
  }
  if ([...value].some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)) {
    throw new Error("Workspace reference contains unsupported control characters.");
  }
  const parts = value.split("/");
  if (parts.some((part) => !part || part === "." || part === "..")) {
    throw new Error("Workspace reference cannot contain traversal or empty path components.");
  }
  return value;
}

function projectResourceAddWorkspacePath() {
  if (!dailyState.selectedProjectId || !dailyState.selectedChatId) return;
  if (projectResourceState.resources.length >= PROJECT_RESOURCE_MAX_ITEMS) {
    projectResourceSetStatus("A work turn can carry at most four resources.");
    return;
  }
  try {
    const sourcePath = projectResourceNormalizeWorkspacePath(dailyById("daily-resource-workspace-path").value);
    if (projectResourceState.resources.some((item) => item.kind === "workspace_file" && item.source_path === sourcePath)) {
      throw new Error("That workspace file is already selected.");
    }
    projectResourceState.resources.push({ kind: "workspace_file", source_path: sourcePath });
    dailyById("daily-resource-workspace-path").value = "";
    projectResourceSelectionChanged();
    projectResourceSetStatus("Relative workspace reference added. Content freezes when the turn is accepted.");
    projectResourceRenderSelection();
  } catch (error) {
    projectResourceSetStatus(dailyMessage(error));
  }
}

function projectResourceBytesToBase64(bytes) {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, Math.min(bytes.length, offset + 0x8000)));
  }
  return btoa(binary);
}

async function projectResourceUploadFiles(fileList) {
  const projectId = dailyState.selectedProjectId;
  const chatId = dailyState.selectedChatId;
  if (!projectId || !chatId) return;
  const generation = projectResourceState.generation;
  const files = Array.from(fileList || []);
  const available = PROJECT_RESOURCE_MAX_ITEMS - projectResourceState.resources.length;
  if (!files.length) return;
  if (files.length > available) {
    projectResourceSetStatus(`Only ${available} more resources can be selected.`);
    dailyById("daily-resource-attachments").value = "";
    return;
  }
  for (const file of files) {
    if (!projectResourceCurrent(projectId, chatId) || generation !== projectResourceState.generation) return;
    if (!(file instanceof File) || file.size < 1 || file.size > PROJECT_RESOURCE_MAX_ATTACHMENT_BYTES) {
      projectResourceSetStatus("Each attachment must be a browser file containing 1 byte to 1 MiB.");
      return;
    }
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      if (bytes.byteLength !== file.size) throw new Error("Attachment byte count changed while reading.");
      const record = await api(`/v1/projects/${encodeURIComponent(projectId)}/attachments`, {
        method: "POST",
        body: JSON.stringify({
          schema_version: "project-attachment-upload-request-v1",
          filename: String(file.name || "attachment"),
          media_type: String(file.type || "application/octet-stream"),
          data_base64: projectResourceBytesToBase64(bytes),
        }),
      });
      if (!projectResourceCurrent(projectId, chatId) || generation !== projectResourceState.generation) return;
      if (!record || record.project_id !== projectId
          || !projectResourceIdPatterns.attachment.test(String(record.attachment_id || ""))
          || record.size_bytes !== file.size) {
        throw new Error("Attachment upload returned inconsistent metadata.");
      }
      projectResourceState.resources.push({
        kind: "attachment",
        attachment_id: record.attachment_id,
        label: String(record.filename || file.name || "attachment"),
        size_bytes: record.size_bytes,
      });
      projectResourceSelectionChanged();
      projectResourceRenderSelection();
      projectResourceSetStatus("Attachment copied into project storage and selected for the next turn.");
    } catch (error) {
      projectResourceSetStatus(`Attachment upload failed: ${dailyMessage(error)}`);
      return;
    }
  }
  dailyById("daily-resource-attachments").value = "";
}

function projectResourceExecutionCollectionPath(path) {
  return /^\/v1\/projects\/project_[0-9a-f]{32}\/chats\/chat_[0-9a-f]{32}\/executions$/.test(path);
}

const projectResourceApiBefore = api;
api = async function apiWithProjectResources(path, options = {}) {
  if (String(options.method || "GET").toUpperCase() === "POST"
      && projectResourceExecutionCollectionPath(path)
      && typeof options.body === "string") {
    let payload = null;
    try { payload = JSON.parse(options.body); } catch (_error) { payload = null; }
    if (payload && payload.schema_version === "conversation-execution-submit-v1"
        && typeof payload.submission_id === "string") {
      let frozen = projectResourceState.submissionResources.get(payload.submission_id);
      if (!frozen) {
        frozen = projectResourceReferences();
        projectResourceState.submissionResources.set(payload.submission_id, frozen);
      }
      const nextPayload = { ...payload };
      if (frozen.length) {
        nextPayload.schema_version = "conversation-execution-submit-v2";
        nextPayload.resources = frozen.map((item) => ({ ...item }));
      }
      try {
        const result = await projectResourceApiBefore(path, { ...options, body: JSON.stringify(nextPayload) });
        projectResourceState.submissionResources.delete(payload.submission_id);
        if (frozen.length && JSON.stringify(projectResourceReferences()) === JSON.stringify(frozen)) {
          projectResourceClearSelection();
        }
        return result;
      } catch (error) {
        if (error && typeof error.status === "number" && error.status < 500) {
          projectResourceState.submissionResources.delete(payload.submission_id);
        }
        throw error;
      }
    }
  }
  return projectResourceApiBefore(path, options);
};

function projectResourceBuildUi() {
  const details = projectResourceElement("details");
  details.id = "daily-project-resources";
  details.className = "daily-inline-form";
  const summary = projectResourceElement("summary", "Files & attachments");
  const note = projectResourceElement("p", "Select up to four resources for the next work turn. Attachments are copied into project storage; workspace files use relative paths only.");
  note.className = "muted small";
  const attachment = projectResourceElement("input");
  attachment.id = "daily-resource-attachments";
  attachment.type = "file";
  attachment.multiple = true;
  attachment.addEventListener("change", () => void projectResourceUploadFiles(attachment.files));
  const attachmentLabel = projectResourceElement("label", "Attach local files");
  attachmentLabel.htmlFor = attachment.id;
  const workspaceLabel = projectResourceElement("label", "Reference workspace file");
  workspaceLabel.htmlFor = "daily-resource-workspace-path";
  const actions = projectResourceElement("div");
  actions.className = "daily-inline-actions";
  const path = projectResourceElement("input");
  path.id = "daily-resource-workspace-path";
  path.type = "text";
  path.maxLength = 1024;
  path.placeholder = "src/example.py";
  path.autocomplete = "off";
  const add = projectResourceElement("button", "Add reference");
  add.type = "button";
  add.className = "secondary compact-button";
  add.addEventListener("click", projectResourceAddWorkspacePath);
  actions.append(path, add);
  const list = projectResourceElement("div");
  list.id = "daily-resource-list";
  const status = projectResourceElement("p");
  status.id = "daily-resource-status";
  status.className = "muted small";
  status.setAttribute("aria-live", "polite");
  details.append(summary, note, attachmentLabel, attachment, workspaceLabel, actions, list, status);
  const composer = document.querySelector(".daily-composer-wrap");
  if (!composer || !composer.parentNode) throw new Error("daily composer wrapper is unavailable");
  composer.parentNode.insertBefore(details, composer);
  projectResourceRenderSelection();
}

projectResourceBuildUi();

const projectResourceSelectChatBefore = dailySelectChat;
dailySelectChat = async function dailySelectChatWithResources(chatId, options) {
  projectResourceClearSelection();
  await projectResourceSelectChatBefore(chatId, options);
};
const projectResourceClearChatBefore = dailyClearChatSelection;
dailyClearChatSelection = function dailyClearChatSelectionWithResources() {
  projectResourceClearSelection();
  projectResourceClearChatBefore();
};
dailyById("lock-button").addEventListener("click", () => {
  projectResourceState.submissionResources.clear();
  projectResourceClearSelection();
});
