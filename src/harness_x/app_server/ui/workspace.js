"use strict";

// M68 everyday product projection. This classic script intentionally reuses the authenticated
// `api` and `unlockOperator` bindings from app.js; it never reads or exports the bearer token.
const dailyState = {
  unlocked: false,
  projects: new Map(),
  chats: new Map(),
  selectedProjectId: null,
  selectedChatId: null,
  loadGeneration: 0,
};

const dailyById = (id) => document.getElementById(id);

function dailyMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function dailySetHidden(id, hidden) {
  dailyById(id).classList.toggle("hidden", hidden);
}

function dailySetText(id, value) {
  dailyById(id).textContent = value == null || value === "" ? "—" : String(value);
}

function dailySetError(value = "") {
  dailyById("daily-error").textContent = value;
}

function dailySetBusy(busy, label = "Loading local workspace…") {
  dailySetHidden("daily-loading", !busy);
  dailyById("daily-loading").textContent = busy ? label : "";
}

function dailyFormatTime(value) {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
}

function dailyActionButton(label, action, className = "secondary compact-button") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

function dailyEmpty(text) {
  const paragraph = document.createElement("p");
  paragraph.className = "muted small daily-list-empty";
  paragraph.textContent = text;
  return paragraph;
}

function dailyShowSurface(name) {
  const showWorkspace = name === "workspace";
  dailySetHidden("daily-surface", !showWorkspace);
  dailySetHidden("advanced-surface", showWorkspace);
  dailyById("show-workspace").classList.toggle("surface-tab--selected", showWorkspace);
  dailyById("show-advanced").classList.toggle("surface-tab--selected", !showWorkspace);
  dailyById("show-workspace").setAttribute("aria-selected", String(showWorkspace));
  dailyById("show-advanced").setAttribute("aria-selected", String(!showWorkspace));
}

function dailyRenderLocked() {
  dailyState.projects.clear();
  dailyState.chats.clear();
  dailyState.selectedProjectId = null;
  dailyState.selectedChatId = null;
  dailySetHidden("daily-locked", false);
  dailySetHidden("daily-content", true);
  dailyById("daily-auth-state").textContent = "Locked";
  dailyById("daily-auth-state").className = "pill pill--muted";
  dailyById("daily-project-list").replaceChildren(dailyEmpty("Unlock Harness X to load projects."));
  dailyById("daily-chat-list").replaceChildren(dailyEmpty("Select a project to load chats."));
  dailyById("daily-message-list").replaceChildren();
  dailySetError("");
  dailySetBusy(false);
}

function dailyRenderUnlocked() {
  dailySetHidden("daily-locked", true);
  dailySetHidden("daily-content", false);
  dailyById("daily-auth-state").textContent = "Local";
  dailyById("daily-auth-state").className = "pill pill--succeeded";
}

function dailyProjectButton(project) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "daily-project-item";
  if (project.project_id === dailyState.selectedProjectId) {
    button.classList.add("daily-project-item--selected");
  }
  button.addEventListener("click", () => {
    void dailySelectProject(project.project_id, { persist: true });
  });

  const name = document.createElement("strong");
  name.textContent = project.name;
  const path = document.createElement("span");
  path.className = "daily-item-subtitle mono";
  path.textContent = project.workspace_root;
  button.append(name, path);
  return button;
}

function dailyRenderProjects() {
  const activeRoot = dailyById("daily-project-list");
  const archivedRoot = dailyById("daily-archived-project-list");
  activeRoot.replaceChildren();
  archivedRoot.replaceChildren();

  const projects = [...dailyState.projects.values()];
  const active = projects.filter((project) => !project.archived);
  const archived = projects.filter((project) => project.archived);

  for (const project of active) activeRoot.append(dailyProjectButton(project));
  if (!active.length) activeRoot.append(dailyEmpty("No projects yet. Add a workspace to begin."));

  for (const project of archived) {
    const row = document.createElement("div");
    row.className = "daily-archived-row";
    const label = document.createElement("span");
    label.textContent = project.name;
    const restore = dailyActionButton("Restore", () => {
      void dailyRestoreProject(project.project_id);
    });
    row.append(label, restore);
    archivedRoot.append(row);
  }
  dailySetHidden("daily-archived-projects", archived.length === 0);
}

function dailyChatButton(chat) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "daily-chat-item";
  if (chat.chat_id === dailyState.selectedChatId) {
    button.classList.add("daily-chat-item--selected");
  }
  button.addEventListener("click", () => {
    void dailySelectChat(chat.chat_id, { persist: true });
  });

  const title = document.createElement("strong");
  title.textContent = chat.title;
  const meta = document.createElement("span");
  meta.className = "daily-item-subtitle";
  meta.textContent = `${chat.message_count} message${chat.message_count === 1 ? "" : "s"}`;
  button.append(title, meta);
  return button;
}

function dailyRenderChats() {
  const activeRoot = dailyById("daily-chat-list");
  const archivedRoot = dailyById("daily-archived-chat-list");
  activeRoot.replaceChildren();
  archivedRoot.replaceChildren();

  const chats = [...dailyState.chats.values()];
  const active = chats.filter((chat) => !chat.archived);
  const archived = chats.filter((chat) => chat.archived);

  for (const chat of active) activeRoot.append(dailyChatButton(chat));
  if (!active.length) activeRoot.append(dailyEmpty("No chats in this project yet."));

  for (const chat of archived) {
    const row = document.createElement("div");
    row.className = "daily-archived-row";
    const label = document.createElement("span");
    label.textContent = chat.title;
    const restore = dailyActionButton("Restore", () => {
      void dailyRestoreChat(chat.chat_id);
    });
    row.append(label, restore);
    archivedRoot.append(row);
  }
  dailySetHidden("daily-archived-chats", archived.length === 0);
}

function dailyClearProjectSelection() {
  dailyState.selectedProjectId = null;
  dailyState.selectedChatId = null;
  dailyState.chats.clear();
  dailyRenderProjects();
  dailyRenderChats();
  dailySetHidden("daily-no-project", false);
  dailySetHidden("daily-project-view", true);
  dailyById("daily-message-list").replaceChildren();
}

function dailyClearChatSelection() {
  dailyState.selectedChatId = null;
  dailyRenderChats();
  dailySetHidden("daily-no-chat", false);
  dailySetHidden("daily-chat-view", true);
  dailyById("daily-message-list").replaceChildren();
  dailyById("daily-composer-text").value = "";
  dailyById("daily-composer-status").textContent = "";
}

function dailyRenderProjectHeader(project) {
  dailySetText("daily-project-name", project.name);
  dailySetText("daily-project-path", project.workspace_root);
  dailyById("daily-project-rename-name").value = project.name;
}

function dailyRenderChatHeader(chat) {
  dailySetText("daily-chat-title", chat.title);
  dailySetText("daily-chat-meta", `Opened ${dailyFormatTime(chat.last_opened_at)} · ${chat.message_count} message${chat.message_count === 1 ? "" : "s"}`);
  dailyById("daily-chat-rename-title").value = chat.title;
}

function dailyMessageText(message) {
  if (!message || !message.content) return "";
  if (message.content.type === "text") return String(message.content.text || "");
  if (message.content.type === "system_notice") return String(message.content.text || "");
  return "Unsupported message content";
}

function dailyRenderMessages(messages) {
  const root = dailyById("daily-message-list");
  root.replaceChildren();
  if (!messages.length) {
    root.append(dailyEmpty("This chat is empty. Write the first message below."));
    return;
  }

  let expectedSequence = 1;
  for (const message of messages) {
    if (message.sequence !== expectedSequence) {
      throw new Error(`chat history is non-contiguous at sequence ${expectedSequence}`);
    }
    expectedSequence += 1;

    const article = document.createElement("article");
    article.className = `daily-message daily-message--${String(message.role)}`;

    const header = document.createElement("div");
    header.className = "daily-message-header";
    const role = document.createElement("strong");
    role.textContent = message.role === "user" ? "You" : message.role === "assistant" ? "Harness X" : "System";
    const meta = document.createElement("span");
    meta.className = "mono muted small";
    meta.textContent = `#${message.sequence} · ${dailyFormatTime(message.created_at)}`;
    header.append(role, meta);

    const body = document.createElement("p");
    body.className = "daily-message-body";
    body.textContent = dailyMessageText(message);
    article.append(header, body);
    root.append(article);
  }
  root.scrollTop = root.scrollHeight;
}

async function dailyLoadMessages(chatId) {
  const projectId = dailyState.selectedProjectId;
  if (!projectId || dailyState.selectedChatId !== chatId) return;
  const page = await api(`/v1/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chatId)}/messages`);
  if (dailyState.selectedChatId !== chatId) return;
  dailyRenderMessages(page.messages || []);
}

async function dailyLoadChats(projectId, preferredChatId = null, persistFallback = false) {
  const page = await api(`/v1/projects/${encodeURIComponent(projectId)}/chats?include_archived=true`);
  if (dailyState.selectedProjectId !== projectId) return;

  dailyState.chats = new Map((page.chats || []).map((chat) => [chat.chat_id, chat]));
  dailyRenderChats();
  const active = [...dailyState.chats.values()].filter((chat) => !chat.archived);
  const preferred = preferredChatId && dailyState.chats.get(preferredChatId);
  if (preferred && !preferred.archived) {
    await dailySelectChat(preferred.chat_id, { persist: false });
    return;
  }
  if (active.length) {
    await dailySelectChat(active[0].chat_id, { persist: persistFallback });
    return;
  }
  dailyClearChatSelection();
}

async function dailySelectProject(projectId, { persist }) {
  const project = dailyState.projects.get(projectId);
  if (!project || project.archived) return;
  dailySetError("");
  dailyState.selectedProjectId = projectId;
  dailyState.selectedChatId = null;
  dailyRenderProjects();
  dailySetHidden("daily-no-project", true);
  dailySetHidden("daily-project-view", false);

  let current = project;
  try {
    if (persist) {
      current = await api(`/v1/projects/${encodeURIComponent(projectId)}/open`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      dailyState.projects.set(projectId, current);
      dailyRenderProjects();
    }
    dailyRenderProjectHeader(current);
    await dailyLoadChats(projectId, current.last_opened_chat_id, true);
  } catch (error) {
    dailySetError(`Project load failed: ${dailyMessage(error)}`);
  }
}

async function dailySelectChat(chatId, { persist }) {
  const projectId = dailyState.selectedProjectId;
  const chat = dailyState.chats.get(chatId);
  if (!projectId || !chat || chat.project_id !== projectId || chat.archived) return;
  dailySetError("");
  dailyState.selectedChatId = chatId;
  dailyRenderChats();
  dailySetHidden("daily-no-chat", true);
  dailySetHidden("daily-chat-view", false);

  let current = chat;
  try {
    if (persist) {
      current = await api(`/v1/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chatId)}/open`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      dailyState.chats.set(chatId, current);
      dailyRenderChats();
    }
    dailyRenderChatHeader(current);
    await dailyLoadMessages(chatId);
  } catch (error) {
    dailySetError(`Chat load failed: ${dailyMessage(error)}`);
  }
}

async function dailyLoadWorkspace() {
  const generation = dailyState.loadGeneration + 1;
  dailyState.loadGeneration = generation;
  dailyRenderUnlocked();
  dailySetBusy(true);
  dailySetError("");
  try {
    const [projectPage, restoration] = await Promise.all([
      api("/v1/projects?include_archived=true"),
      api("/v1/product/restoration"),
    ]);
    if (generation !== dailyState.loadGeneration || !dailyState.unlocked) return;

    dailyState.projects = new Map((projectPage.projects || []).map((project) => [project.project_id, project]));
    dailyRenderProjects();
    const active = [...dailyState.projects.values()].filter((project) => !project.archived);
    const restored = restoration.last_opened_project_id
      ? dailyState.projects.get(restoration.last_opened_project_id)
      : null;
    if (restored && !restored.archived) {
      await dailySelectProject(restored.project_id, { persist: false });
    } else if (active.length) {
      await dailySelectProject(active[0].project_id, { persist: true });
    } else {
      dailyClearProjectSelection();
    }
  } catch (error) {
    if (generation === dailyState.loadGeneration) {
      dailySetError(`Workspace load failed: ${dailyMessage(error)}`);
      dailyClearProjectSelection();
    }
  } finally {
    if (generation === dailyState.loadGeneration) dailySetBusy(false);
  }
}

async function dailyReloadProjects(selectProjectId = null) {
  const projectPage = await api("/v1/projects?include_archived=true");
  dailyState.projects = new Map((projectPage.projects || []).map((project) => [project.project_id, project]));
  dailyRenderProjects();
  if (selectProjectId) {
    await dailySelectProject(selectProjectId, { persist: true });
    return;
  }
  const selected = dailyState.selectedProjectId && dailyState.projects.get(dailyState.selectedProjectId);
  if (selected && !selected.archived) {
    await dailySelectProject(selected.project_id, { persist: false });
    return;
  }
  const active = [...dailyState.projects.values()].filter((project) => !project.archived);
  if (active.length) await dailySelectProject(active[0].project_id, { persist: true });
  else dailyClearProjectSelection();
}

async function dailyReloadChats(selectChatId = null) {
  const projectId = dailyState.selectedProjectId;
  if (!projectId) return;
  const page = await api(`/v1/projects/${encodeURIComponent(projectId)}/chats?include_archived=true`);
  dailyState.chats = new Map((page.chats || []).map((chat) => [chat.chat_id, chat]));
  dailyRenderChats();
  if (selectChatId) {
    await dailySelectChat(selectChatId, { persist: true });
    return;
  }
  const selected = dailyState.selectedChatId && dailyState.chats.get(dailyState.selectedChatId);
  if (selected && !selected.archived) {
    await dailySelectChat(selected.chat_id, { persist: false });
    return;
  }
  const active = [...dailyState.chats.values()].filter((chat) => !chat.archived);
  if (active.length) await dailySelectChat(active[0].chat_id, { persist: true });
  else dailyClearChatSelection();
}

async function dailyRestoreProject(projectId) {
  dailySetError("");
  try {
    await api(`/v1/projects/${encodeURIComponent(projectId)}/restore`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await dailyReloadProjects(projectId);
  } catch (error) {
    dailySetError(`Project restore failed: ${dailyMessage(error)}`);
  }
}

async function dailyRestoreChat(chatId) {
  const projectId = dailyState.selectedProjectId;
  if (!projectId) return;
  dailySetError("");
  try {
    await api(`/v1/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chatId)}/restore`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await dailyReloadChats(chatId);
  } catch (error) {
    dailySetError(`Chat restore failed: ${dailyMessage(error)}`);
  }
}

dailyById("show-workspace").addEventListener("click", () => dailyShowSurface("workspace"));
dailyById("show-advanced").addEventListener("click", () => dailyShowSurface("advanced"));
dailyById("daily-open-advanced-auth").addEventListener("click", () => {
  dailyShowSurface("advanced");
  dailyById("token").focus();
});

dailyById("daily-new-project-button").addEventListener("click", () => {
  dailySetHidden("daily-project-create", false);
  dailyById("daily-project-create-name").focus();
});
dailyById("daily-project-create-cancel").addEventListener("click", () => {
  dailySetHidden("daily-project-create", true);
  dailyById("daily-project-create").reset();
});
dailyById("daily-project-create").addEventListener("submit", async (event) => {
  event.preventDefault();
  dailySetError("");
  const data = new FormData(event.currentTarget);
  const payload = {
    schema_version: "create-project-request-v1",
    name: String(data.get("name") || "").trim(),
    workspace_root: String(data.get("workspace_root") || "").trim(),
  };
  try {
    const created = await api("/v1/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    event.currentTarget.reset();
    dailySetHidden("daily-project-create", true);
    await dailyReloadProjects(created.project_id);
  } catch (error) {
    dailySetError(`Project creation failed: ${dailyMessage(error)}`);
  }
});

dailyById("daily-project-rename-button").addEventListener("click", () => {
  dailySetHidden("daily-project-rename", false);
  dailyById("daily-project-rename-name").focus();
});
dailyById("daily-project-rename-cancel").addEventListener("click", () => {
  dailySetHidden("daily-project-rename", true);
});
dailyById("daily-project-rename").addEventListener("submit", async (event) => {
  event.preventDefault();
  const projectId = dailyState.selectedProjectId;
  if (!projectId) return;
  const data = new FormData(event.currentTarget);
  try {
    const renamed = await api(`/v1/projects/${encodeURIComponent(projectId)}/rename`, {
      method: "POST",
      body: JSON.stringify({
        schema_version: "rename-project-request-v1",
        name: String(data.get("name") || "").trim(),
      }),
    });
    dailyState.projects.set(projectId, renamed);
    dailyRenderProjects();
    dailyRenderProjectHeader(renamed);
    dailySetHidden("daily-project-rename", true);
  } catch (error) {
    dailySetError(`Project rename failed: ${dailyMessage(error)}`);
  }
});

dailyById("daily-project-archive-button").addEventListener("click", async () => {
  const projectId = dailyState.selectedProjectId;
  if (!projectId) return;
  const project = dailyState.projects.get(projectId);
  if (!project || !window.confirm(`Archive project “${project.name}”? Chats and history remain restorable.`)) return;
  try {
    await api(`/v1/projects/${encodeURIComponent(projectId)}/archive`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await dailyReloadProjects();
  } catch (error) {
    dailySetError(`Project archive failed: ${dailyMessage(error)}`);
  }
});

dailyById("daily-new-chat-button").addEventListener("click", () => {
  dailySetHidden("daily-chat-create", false);
  dailyById("daily-chat-create-title").focus();
});
dailyById("daily-chat-create-cancel").addEventListener("click", () => {
  dailySetHidden("daily-chat-create", true);
  dailyById("daily-chat-create").reset();
});
dailyById("daily-chat-create").addEventListener("submit", async (event) => {
  event.preventDefault();
  const projectId = dailyState.selectedProjectId;
  if (!projectId) return;
  const data = new FormData(event.currentTarget);
  try {
    const created = await api(`/v1/projects/${encodeURIComponent(projectId)}/chats`, {
      method: "POST",
      body: JSON.stringify({
        schema_version: "create-chat-request-v1",
        title: String(data.get("title") || "").trim(),
      }),
    });
    event.currentTarget.reset();
    dailySetHidden("daily-chat-create", true);
    await dailyReloadChats(created.chat_id);
  } catch (error) {
    dailySetError(`Chat creation failed: ${dailyMessage(error)}`);
  }
});

dailyById("daily-chat-rename-button").addEventListener("click", () => {
  dailySetHidden("daily-chat-rename", false);
  dailyById("daily-chat-rename-title").focus();
});
dailyById("daily-chat-rename-cancel").addEventListener("click", () => {
  dailySetHidden("daily-chat-rename", true);
});
dailyById("daily-chat-rename").addEventListener("submit", async (event) => {
  event.preventDefault();
  const projectId = dailyState.selectedProjectId;
  const chatId = dailyState.selectedChatId;
  if (!projectId || !chatId) return;
  const data = new FormData(event.currentTarget);
  try {
    const renamed = await api(`/v1/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chatId)}/rename`, {
      method: "POST",
      body: JSON.stringify({
        schema_version: "rename-chat-request-v1",
        title: String(data.get("title") || "").trim(),
      }),
    });
    dailyState.chats.set(chatId, renamed);
    dailyRenderChats();
    dailyRenderChatHeader(renamed);
    dailySetHidden("daily-chat-rename", true);
  } catch (error) {
    dailySetError(`Chat rename failed: ${dailyMessage(error)}`);
  }
});

dailyById("daily-chat-archive-button").addEventListener("click", async () => {
  const projectId = dailyState.selectedProjectId;
  const chatId = dailyState.selectedChatId;
  if (!projectId || !chatId) return;
  const chat = dailyState.chats.get(chatId);
  if (!chat || !window.confirm(`Archive chat “${chat.title}”? Its history remains restorable.`)) return;
  try {
    await api(`/v1/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chatId)}/archive`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await dailyReloadChats();
  } catch (error) {
    dailySetError(`Chat archive failed: ${dailyMessage(error)}`);
  }
});

dailyById("daily-composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const projectId = dailyState.selectedProjectId;
  const chatId = dailyState.selectedChatId;
  const textArea = dailyById("daily-composer-text");
  const text = textArea.value;
  if (!projectId || !chatId || !text.trim()) return;

  const send = dailyById("daily-send-message");
  send.disabled = true;
  dailyById("daily-composer-status").textContent = "Saving message locally…";
  try {
    await api(`/v1/projects/${encodeURIComponent(projectId)}/chats/${encodeURIComponent(chatId)}/messages`, {
      method: "POST",
      body: JSON.stringify({
        schema_version: "append-user-message-request-v1",
        role: "user",
        content: {
          type: "text",
          text,
        },
      }),
    });
    textArea.value = "";
    await dailyLoadMessages(chatId);
    await dailyReloadChats(chatId);
    dailyById("daily-composer-status").textContent = "Saved locally. Harness X execution from chat is not connected until M69.";
  } catch (error) {
    dailyById("daily-composer-status").textContent = `Message save failed: ${dailyMessage(error)}`;
  } finally {
    send.disabled = false;
    textArea.focus();
  }
});

const dailyUnlockBeforeWorkspace = unlockOperator;
unlockOperator = async function unlockOperatorWithEverydayWorkspace(token) {
  await dailyUnlockBeforeWorkspace(token);
  dailyState.unlocked = true;
  await dailyLoadWorkspace();
};

dailyById("lock-button").addEventListener("click", () => {
  dailyState.unlocked = false;
  dailyState.loadGeneration += 1;
  dailyRenderLocked();
});

dailyShowSurface("workspace");
dailyRenderLocked();
