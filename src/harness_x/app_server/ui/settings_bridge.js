"use strict";

// M73 everyday project settings. The browser selects only software-owned profile/policy IDs;
// endpoint configuration, credentials, environment variables, commands, and runtime limits remain
// server-owned and are intentionally absent from this surface.
const projectSettingsState = {
  profiles: new Map(),
  loadedProfiles: false,
  generation: 0,
};

function projectSettingsElement(tag, text = "") {
  const element = document.createElement(tag);
  if (text) element.textContent = text;
  return element;
}

function projectSettingsBuildUi() {
  const details = projectSettingsElement("details");
  details.id = "daily-project-settings";
  details.className = "daily-inline-form";

  const summary = projectSettingsElement("summary", "Project settings");
  const note = projectSettingsElement(
    "p",
    "Settings apply to future Harness X work. An execution keeps the settings snapshot it started with.",
  );
  note.className = "muted small";

  const form = projectSettingsElement("form");
  form.id = "daily-settings-form";
  form.className = "stack";

  const profileLabel = projectSettingsElement("label", "Model profile");
  profileLabel.htmlFor = "daily-settings-model-profile";
  const profile = projectSettingsElement("select");
  profile.id = "daily-settings-model-profile";
  profile.name = "model_profile";
  profile.required = true;
  const profileDetail = projectSettingsElement("p", "Profiles load after local unlock.");
  profileDetail.id = "daily-settings-profile-detail";
  profileDetail.className = "muted small";

  const connectionActions = projectSettingsElement("div");
  connectionActions.className = "daily-inline-actions";
  const testConnection = projectSettingsElement("button", "Test connection");
  testConnection.id = "daily-settings-test-connection";
  testConnection.type = "button";
  testConnection.className = "secondary compact-button";
  testConnection.disabled = true;
  const connectionStatus = projectSettingsElement("span");
  connectionStatus.id = "daily-settings-connection-status";
  connectionStatus.className = "muted small";
  connectionStatus.setAttribute("aria-live", "polite");
  connectionActions.append(testConnection, connectionStatus);

  const verificationLabel = projectSettingsElement("label", "Verification strategy");
  verificationLabel.htmlFor = "daily-settings-verification";
  const verification = projectSettingsElement("select");
  verification.id = "daily-settings-verification";
  verification.name = "verification_strategy";
  for (const [value, label] of [
    ["diff_check", "Diff integrity check"],
    ["pytest", "Python test suite"],
    ["pytest_and_diff_check", "Python tests + diff integrity"],
  ]) {
    const option = projectSettingsElement("option", label);
    option.value = value;
    verification.append(option);
  }

  const autonomyLabel = projectSettingsElement("label", "Autonomy profile");
  autonomyLabel.htmlFor = "daily-settings-autonomy";
  const autonomy = projectSettingsElement("select");
  autonomy.id = "daily-settings-autonomy";
  autonomy.name = "autonomy_profile";
  for (const [value, label] of [
    ["standard", "Standard"],
    ["cautious", "Cautious"],
  ]) {
    const option = projectSettingsElement("option", label);
    option.value = value;
    autonomy.append(option);
  }
  const autonomyNote = projectSettingsElement(
    "p",
    "Cautious uses smaller bounded reasoning and tool budgets. Sensitive actions still require the inherited approval policy.",
  );
  autonomyNote.className = "muted small";

  const instructionsLabel = projectSettingsElement("label", "Project instructions");
  instructionsLabel.htmlFor = "daily-settings-instructions";
  const instructions = projectSettingsElement("textarea");
  instructions.id = "daily-settings-instructions";
  instructions.name = "project_instructions";
  instructions.rows = 5;
  instructions.maxLength = 6000;
  instructions.placeholder = "Durable guidance that should accompany future work in this project.";

  const actions = projectSettingsElement("div");
  actions.className = "daily-inline-actions";
  const save = projectSettingsElement("button", "Save project settings");
  save.id = "daily-settings-save";
  save.type = "submit";
  const status = projectSettingsElement("span");
  status.id = "daily-settings-status";
  status.className = "muted small";
  status.setAttribute("aria-live", "polite");
  actions.append(save, status);

  form.append(
    profileLabel,
    profile,
    profileDetail,
    connectionActions,
    verificationLabel,
    verification,
    autonomyLabel,
    autonomy,
    autonomyNote,
    instructionsLabel,
    instructions,
    actions,
  );
  details.append(summary, note, form);

  const renameForm = dailyById("daily-project-rename");
  renameForm.insertAdjacentElement("afterend", details);

  profile.addEventListener("change", () => {
    projectSettingsRenderProfile(profile.value);
    connectionStatus.textContent = "";
  });
  testConnection.addEventListener("click", () => {
    void projectSettingsTestConnection();
  });
  form.addEventListener("submit", (event) => {
    void projectSettingsSave(event);
  });
}

function projectSettingsRenderProfile(profileId) {
  const profile = projectSettingsState.profiles.get(profileId);
  const detail = dailyById("daily-settings-profile-detail");
  const testButton = dailyById("daily-settings-test-connection");
  const saveButton = dailyById("daily-settings-save");
  if (!profile) {
    detail.textContent = "This legacy project profile is not available in the current built-in registry. Choose a current profile before saving or starting new work.";
    testButton.disabled = true;
    saveButton.disabled = true;
    return;
  }
  saveButton.disabled = false;
  const capabilities = Array.isArray(profile.capabilities) && profile.capabilities.length
    ? profile.capabilities.join(", ")
    : "general";
  const keyNote = profile.requires_api_key ? " · configured server credential required" : "";
  detail.textContent = `${profile.provider} · ${profile.model} · ${capabilities}${keyNote}. ${profile.description}`;
  testButton.disabled = !profile.connection_test_supported;
}

async function projectSettingsEnsureProfiles() {
  if (projectSettingsState.loadedProfiles) return;
  const page = await api("/v1/model-profiles");
  projectSettingsState.profiles = new Map(
    (page.profiles || []).map((profile) => [profile.profile_id, profile]),
  );
  projectSettingsState.loadedProfiles = true;
  const select = dailyById("daily-settings-model-profile");
  select.replaceChildren();
  for (const profile of projectSettingsState.profiles.values()) {
    const option = projectSettingsElement(
      "option",
      `${profile.profile_id} — ${profile.model}`,
    );
    option.value = profile.profile_id;
    select.append(option);
  }
}

function projectSettingsSelectProfile(profileId) {
  const select = dailyById("daily-settings-model-profile");
  for (const option of select.querySelectorAll("option[data-legacy-profile]")) {
    option.remove();
  }
  if (!projectSettingsState.profiles.has(profileId)) {
    const legacy = projectSettingsElement("option", `${profileId} — legacy/unavailable`);
    legacy.value = profileId;
    legacy.dataset.legacyProfile = "true";
    select.prepend(legacy);
  }
  select.value = profileId;
  projectSettingsRenderProfile(profileId);
}

async function projectSettingsLoad(projectId) {
  const generation = projectSettingsState.generation + 1;
  projectSettingsState.generation = generation;
  dailyById("daily-settings-status").textContent = "Loading settings…";
  dailyById("daily-settings-connection-status").textContent = "";
  try {
    await projectSettingsEnsureProfiles();
    const settings = await api(`/v1/projects/${encodeURIComponent(projectId)}/settings`);
    if (
      generation !== projectSettingsState.generation
      || dailyState.selectedProjectId !== projectId
    ) return;
    projectSettingsSelectProfile(settings.model_profile);
    dailyById("daily-settings-verification").value = settings.verification_strategy;
    dailyById("daily-settings-autonomy").value = settings.autonomy_profile;
    dailyById("daily-settings-instructions").value = settings.project_instructions || "";
    dailyById("daily-settings-status").textContent = `Revision ${settings.revision}`;
  } catch (error) {
    if (generation === projectSettingsState.generation) {
      dailyById("daily-settings-status").textContent = "Settings unavailable.";
      dailySetError(`Project settings load failed: ${dailyMessage(error)}`);
    }
  }
}

async function projectSettingsSave(event) {
  event.preventDefault();
  const projectId = dailyState.selectedProjectId;
  if (!projectId) return;
  const form = event.currentTarget;
  const data = new FormData(form);
  const save = dailyById("daily-settings-save");
  save.disabled = true;
  dailyById("daily-settings-status").textContent = "Saving settings…";
  dailySetError("");
  try {
    const settings = await api(`/v1/projects/${encodeURIComponent(projectId)}/settings`, {
      method: "POST",
      body: JSON.stringify({
        schema_version: "replace-project-settings-request-v1",
        model_profile: String(data.get("model_profile") || ""),
        verification_strategy: String(data.get("verification_strategy") || ""),
        project_instructions: String(data.get("project_instructions") || ""),
        autonomy_profile: String(data.get("autonomy_profile") || ""),
      }),
    });
    if (dailyState.selectedProjectId !== projectId) return;
    projectSettingsSelectProfile(settings.model_profile);
    dailyById("daily-settings-status").textContent = `Saved revision ${settings.revision}`;
  } catch (error) {
    dailyById("daily-settings-status").textContent = "Save failed.";
    dailySetError(`Project settings save failed: ${dailyMessage(error)}`);
  } finally {
    const current = projectSettingsState.profiles.get(
      dailyById("daily-settings-model-profile").value,
    );
    save.disabled = !current;
  }
}

async function projectSettingsTestConnection() {
  const projectId = dailyState.selectedProjectId;
  const profileId = dailyById("daily-settings-model-profile").value;
  const profile = projectSettingsState.profiles.get(profileId);
  if (!projectId || !profile || !profile.connection_test_supported) return;
  const button = dailyById("daily-settings-test-connection");
  const status = dailyById("daily-settings-connection-status");
  button.disabled = true;
  status.textContent = "Testing configured connection…";
  dailySetError("");
  try {
    const result = await api(
      `/v1/projects/${encodeURIComponent(projectId)}/settings/test-connection`,
      {
        method: "POST",
        body: JSON.stringify({
          schema_version: "model-profile-connection-test-request-v1",
          profile_id: profileId,
        }),
      },
    );
    if (dailyState.selectedProjectId !== projectId) return;
    if (result.ready) {
      const count = Array.isArray(result.advertised_model_ids)
        ? result.advertised_model_ids.length
        : 0;
      status.textContent = count
        ? `Ready · ${count} model${count === 1 ? "" : "s"} advertised`
        : "Ready";
    } else {
      status.textContent = result.supported
        ? "Not ready. Check the configured local/hosted runtime outside this browser surface."
        : "Connection testing is not available for this profile backend.";
    }
  } catch (error) {
    status.textContent = "Connection test failed.";
    dailySetError(`Model connection test failed: ${dailyMessage(error)}`);
  } finally {
    const current = projectSettingsState.profiles.get(
      dailyById("daily-settings-model-profile").value,
    );
    button.disabled = !current || !current.connection_test_supported;
  }
}

projectSettingsBuildUi();

const projectSettingsRenderProjectHeaderBefore = dailyRenderProjectHeader;
dailyRenderProjectHeader = function dailyRenderProjectHeaderWithSettings(project) {
  projectSettingsRenderProjectHeaderBefore(project);
  void projectSettingsLoad(project.project_id);
};
