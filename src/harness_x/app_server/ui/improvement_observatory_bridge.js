"use strict";

// M76 presentation-only observatory. The server chooses the project workspace and evidence
// root. This bridge performs only an explicit GET and keeps the returned projection in memory.
const improvementObservatoryState = { projectId: null, projection: null, loading: false };
const improvementObservatoryProjectPattern = /^project_[0-9a-f]{32}$/;

function improvementObservatoryNode(tag, text = "", className = "") {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function improvementObservatoryPath(projectId) {
  if (!improvementObservatoryProjectPattern.test(String(projectId || ""))) {
    throw new Error("Invalid observatory project identity.");
  }
  return `/v1/projects/${projectId}/improvement-observatory`;
}

function improvementObservatoryEnsureUi() {
  let root = document.getElementById("daily-improvement-observatory");
  if (root) return root;
  const projectView = dailyById("daily-project-view");
  const toolbar = projectView.querySelector(".daily-project-toolbar");
  root = improvementObservatoryNode("section", "", "daily-inline-form");
  root.id = "daily-improvement-observatory";
  root.setAttribute("aria-label", "Improvement Observatory");

  const heading = improvementObservatoryNode("div", "", "daily-inline-actions");
  const label = improvementObservatoryNode("div");
  label.append(
    improvementObservatoryNode("p", "Improvement Observatory", "eyebrow"),
    improvementObservatoryNode("p", "Read-only view of existing bounded improvement evidence.", "muted small"),
  );
  const refresh = improvementObservatoryNode("button", "Refresh observatory", "secondary compact-button");
  refresh.id = "daily-improvement-observatory-refresh";
  refresh.type = "button";
  refresh.addEventListener("click", () => void improvementObservatoryRefresh());
  heading.append(label, refresh);

  const status = improvementObservatoryNode("p", "No observatory read has been requested.", "muted small");
  status.id = "daily-improvement-observatory-status";
  status.setAttribute("aria-live", "polite");
  const content = improvementObservatoryNode("div");
  content.id = "daily-improvement-observatory-content";
  root.append(heading, status, content);
  toolbar.insertAdjacentElement("afterend", root);
  return root;
}

function improvementObservatorySection(title, rows, summary, emptyText) {
  const section = improvementObservatoryNode("section");
  section.append(improvementObservatoryNode("strong", title));
  if (!rows.length) {
    section.append(improvementObservatoryNode("p", emptyText, "muted small"));
    return section;
  }
  for (const item of rows) section.append(improvementObservatoryNode("p", summary(item), "small"));
  return section;
}

function improvementObservatoryRender() {
  improvementObservatoryEnsureUi();
  const projectId = dailyState.selectedProjectId;
  const status = dailyById("daily-improvement-observatory-status");
  const content = dailyById("daily-improvement-observatory-content");
  const refresh = dailyById("daily-improvement-observatory-refresh");
  refresh.disabled = improvementObservatoryState.loading || !projectId;
  content.replaceChildren();
  if (!projectId) {
    status.textContent = "Select a project to inspect existing improvement evidence.";
    return;
  }
  const projection = improvementObservatoryState.projectId === projectId
    ? improvementObservatoryState.projection
    : null;
  if (!projection) {
    status.textContent = "No observatory read has been requested for this project.";
    return;
  }
  status.textContent = projection.observatory_root_present
    ? `Read-only snapshot · Harness X ${projection.software_version}${projection.scan_truncated ? " · bounded scan truncated" : ""}`
    : `Read-only snapshot · Harness X ${projection.software_version} · no .harness-x evidence root observed`;

  content.append(
    improvementObservatorySection("Observed versions", projection.versions || [],
      (x) => `${x.system_version} · ${x.source_kind} · ${x.source}`, "No version evidence observed."),
    improvementObservatorySection("Diagnosed weaknesses", projection.weaknesses || [],
      (x) => `${x.procedure_id} · ${x.reason || "suspended procedure"}`, "No suspended procedure observed."),
    improvementObservatorySection("Candidates", projection.candidates || [],
      (x) => `${x.candidate_id} · ${x.candidate_kind} · ${x.status}`, "No candidate evidence observed."),
    improvementObservatorySection("Experiments and regressions", projection.experiments || [],
      (x) => `${x.candidate_id} · ${x.disposition} · ${(x.regressions || []).length} regression(s)`, "No experiment evidence observed."),
    improvementObservatorySection("Promotion and rollback evidence", projection.promotions || [],
      (x) => `${x.promotion_id} · ${x.status} · rollback ${x.rollback && x.rollback.independently_verified === true ? "verified" : "not independently verified"}`, "No promotion evidence observed."),
    improvementObservatorySection("Procedure-improvement campaigns", projection.campaigns || [],
      (x) => `${x.campaign_id} · ${x.status} · proposals ${x.proposal_attempts}/${x.max_candidate_proposals} · trials ${x.trial_attempts}/${x.max_trial_tasks}`, "No campaign evidence observed."),
    improvementObservatorySection("Source health", projection.sources || [],
      (x) => `${x.relative_path} · ${x.status}`, "No allowlisted source observed."),
  );
}

async function improvementObservatoryRefresh() {
  const projectId = dailyState.selectedProjectId;
  if (!projectId || !dailyState.unlocked) return;
  improvementObservatoryState.loading = true;
  improvementObservatoryRender();
  try {
    const projection = await api(improvementObservatoryPath(projectId));
    if (dailyState.selectedProjectId !== projectId) return;
    if (!projection || projection.schema_version !== "improvement-observatory-v1"
        || projection.project_id !== projectId || projection.read_only !== true
        || projection.promotion_authority !== false || !Array.isArray(projection.sources)) {
      throw new Error("Observatory projection identity is inconsistent.");
    }
    improvementObservatoryState.projectId = projectId;
    improvementObservatoryState.projection = projection;
  } catch (error) {
    if (dailyState.selectedProjectId === projectId) {
      improvementObservatoryState.projectId = projectId;
      improvementObservatoryState.projection = null;
      dailyById("daily-improvement-observatory-status").textContent = `Observatory read failed: ${dailyMessage(error)}`;
    }
  } finally {
    improvementObservatoryState.loading = false;
    if (dailyState.selectedProjectId === projectId) improvementObservatoryRender();
  }
}

function improvementObservatoryReset() {
  improvementObservatoryState.projectId = null;
  improvementObservatoryState.projection = null;
  improvementObservatoryState.loading = false;
  improvementObservatoryRender();
}

const improvementObservatorySelectProjectBefore = dailySelectProject;
dailySelectProject = async function dailySelectProjectWithObservatory(projectId, options) {
  if (dailyState.selectedProjectId !== projectId) improvementObservatoryReset();
  await improvementObservatorySelectProjectBefore(projectId, options);
  improvementObservatoryRender();
};

const improvementObservatoryClearProjectBefore = dailyClearProjectSelection;
dailyClearProjectSelection = function dailyClearProjectSelectionWithObservatory() {
  improvementObservatoryReset();
  improvementObservatoryClearProjectBefore();
};

dailyById("lock-button").addEventListener("click", improvementObservatoryReset);
improvementObservatoryEnsureUi();
improvementObservatoryRender();
