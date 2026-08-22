"use strict";

const evidenceManifestState = {
  token: null,
  generation: 0,
  controller: null,
};

const evidenceManifestById = (id) => document.getElementById(id);
const evidenceManifestButton = evidenceManifestById("download-evidence-manifest");
const evidenceManifestStatus = evidenceManifestById("evidence-manifest-status");
const evidenceManifestShaPattern = /^[0-9a-f]{64}$/;
const evidenceManifestTerminalStates = new Set(["succeeded", "failed", "cancelled"]);
const evidenceManifestAvailabilityStates = new Set(["available", "not_available"]);

function evidenceManifestSessionId() {
  return evidenceManifestById("session-id").textContent.trim();
}

function evidenceManifestSessionStatus() {
  return evidenceManifestById("session-status").textContent.trim();
}

function evidenceManifestIsCurrent(sessionId, generation) {
  return (
    evidenceManifestState.generation === generation
    && evidenceManifestSessionId() === sessionId
  );
}

function cancelEvidenceManifestDownload() {
  if (evidenceManifestState.controller) evidenceManifestState.controller.abort();
  evidenceManifestState.controller = null;
}

function updateEvidenceManifestAvailability() {
  evidenceManifestButton.disabled = (
    !evidenceManifestState.token
    || !evidenceManifestSessionId()
    || !evidenceManifestTerminalStates.has(evidenceManifestSessionStatus())
  );
}

function evidenceManifestMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

async function evidenceManifestResponseError(response) {
  const fallback = `HTTP ${response.status}`;
  try {
    const payload = await response.json();
    return payload.detail || payload.error || fallback;
  } catch (_error) {
    return fallback;
  }
}

async function evidenceManifestSha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(
    new Uint8Array(digest),
    (item) => item.toString(16).padStart(2, "0"),
  ).join("");
}

async function downloadEvidenceManifest() {
  const sessionId = evidenceManifestSessionId();
  if (
    !evidenceManifestState.token
    || !sessionId
    || !evidenceManifestTerminalStates.has(evidenceManifestSessionStatus())
  ) return;

  const generation = evidenceManifestState.generation;
  cancelEvidenceManifestDownload();
  const controller = new AbortController();
  evidenceManifestState.controller = controller;

  evidenceManifestButton.disabled = true;
  evidenceManifestStatus.textContent = "Preparing authenticated evidence manifest…";
  try {
    const response = await fetch(
      `/v1/sessions/${encodeURIComponent(sessionId)}/evidence/manifest`,
      {
        method: "GET",
        headers: {
          Authorization: `Bearer ${evidenceManifestState.token}`,
          Accept: "application/json",
        },
        cache: "no-store",
        credentials: "omit",
        signal: controller.signal,
      },
    );
    if (!evidenceManifestIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    if (response.status === 401) {
      evidenceManifestState.token = null;
      throw new Error("operator token was rejected; unlock the operator view again");
    }
    if (!response.ok) throw new Error(await evidenceManifestResponseError(response));
    if (!evidenceManifestIsCurrent(sessionId, generation) || controller.signal.aborted) return;

    const contentType = response.headers.get("Content-Type") || "";
    if (contentType.toLowerCase() !== "application/json; charset=utf-8") {
      throw new Error("evidence manifest returned an unexpected content type");
    }
    if (
      response.headers.get("Content-Disposition")
      !== 'attachment; filename="session-evidence-manifest.json"'
    ) {
      throw new Error("evidence manifest returned an unexpected filename");
    }

    const sourceSha256 = (
      response.headers.get("X-Harness-X-Evidence-Manifest-SHA256") || ""
    );
    const rawLength = response.headers.get("Content-Length");
    if (!evidenceManifestShaPattern.test(sourceSha256)) {
      throw new Error("evidence manifest returned an invalid response SHA-256");
    }
    if (rawLength == null || !/^(0|[1-9][0-9]*)$/.test(rawLength)) {
      throw new Error("evidence manifest returned an invalid content length");
    }

    const bytes = await response.arrayBuffer();
    if (!evidenceManifestIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    const declaredLength = Number.parseInt(rawLength, 10);
    if (!Number.isSafeInteger(declaredLength) || bytes.byteLength !== declaredLength) {
      throw new Error("evidence manifest byte count does not match Content-Length");
    }
    const observedSha256 = await evidenceManifestSha256Hex(bytes);
    if (!evidenceManifestIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    if (observedSha256 !== sourceSha256) {
      throw new Error("evidence manifest bytes do not match the declared SHA-256");
    }

    let manifest;
    try {
      manifest = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    } catch (_error) {
      throw new Error("evidence manifest body is not valid UTF-8 JSON");
    }
    if (manifest.schema_version !== "app-terminal-evidence-manifest-v1") {
      throw new Error("evidence manifest returned an unexpected schema version");
    }
    if (manifest.session_id !== sessionId) {
      throw new Error("evidence manifest belongs to a different session");
    }
    if (!evidenceManifestShaPattern.test(manifest.fingerprint || "")) {
      throw new Error("evidence manifest returned an invalid self-fingerprint");
    }
    if (
      !manifest.lifecycle
      || !evidenceManifestTerminalStates.has(manifest.lifecycle.status)
      || !evidenceManifestShaPattern.test(manifest.lifecycle.ledger_head_hash || "")
    ) {
      throw new Error("evidence manifest returned invalid lifecycle evidence");
    }
    if (
      !manifest.coding_report
      || !evidenceManifestAvailabilityStates.has(manifest.coding_report.availability)
      || !manifest.causal_trace
      || !evidenceManifestAvailabilityStates.has(manifest.causal_trace.availability)
    ) {
      throw new Error("evidence manifest returned invalid component availability");
    }

    const blob = new Blob([bytes], { type: "application/json;charset=utf-8" });
    const objectUrl = URL.createObjectURL(blob);
    try {
      if (!evidenceManifestIsCurrent(sessionId, generation) || controller.signal.aborted) return;
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = "session-evidence-manifest.json";
      document.body.append(link);
      link.click();
      link.remove();
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
    if (!evidenceManifestIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    evidenceManifestStatus.textContent = (
      `Downloaded manifest · report ${manifest.coding_report.availability}`
      + ` · trace ${manifest.causal_trace.availability} · sha256 ${sourceSha256}`
    );
  } catch (error) {
    if (!evidenceManifestIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    evidenceManifestStatus.textContent = (
      `Evidence manifest download failed: ${evidenceManifestMessage(error)}`
    );
  } finally {
    if (evidenceManifestState.controller === controller) evidenceManifestState.controller = null;
    updateEvidenceManifestAvailability();
  }
}

evidenceManifestById("auth-form").addEventListener("submit", () => {
  const token = evidenceManifestById("token").value.trim();
  if (token) evidenceManifestState.token = token;
  updateEvidenceManifestAvailability();
});

evidenceManifestById("lock-button").addEventListener("click", () => {
  evidenceManifestState.generation += 1;
  cancelEvidenceManifestDownload();
  evidenceManifestState.token = null;
  evidenceManifestStatus.textContent = "";
  updateEvidenceManifestAvailability();
});

new MutationObserver(() => {
  evidenceManifestState.generation += 1;
  cancelEvidenceManifestDownload();
  evidenceManifestStatus.textContent = "";
  updateEvidenceManifestAvailability();
}).observe(evidenceManifestById("session-id"), {
  childList: true,
  characterData: true,
  subtree: true,
});

new MutationObserver(updateEvidenceManifestAvailability).observe(
  evidenceManifestById("session-status"),
  {
    childList: true,
    characterData: true,
    subtree: true,
  },
);

new MutationObserver(() => {
  if (evidenceManifestById("auth-state").textContent.trim() === "Locked") {
    evidenceManifestState.generation += 1;
    cancelEvidenceManifestDownload();
    evidenceManifestState.token = null;
    evidenceManifestStatus.textContent = "";
  }
  updateEvidenceManifestAvailability();
}).observe(evidenceManifestById("auth-state"), {
  childList: true,
  characterData: true,
  subtree: true,
});

evidenceManifestButton.addEventListener("click", () => {
  void downloadEvidenceManifest();
});

window.addEventListener("beforeunload", cancelEvidenceManifestDownload);
updateEvidenceManifestAvailability();
