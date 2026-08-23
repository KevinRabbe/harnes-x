"use strict";

const snapshotExportState = {
  token: null,
  generation: 0,
  controller: null,
};

const snapshotExportById = (id) => document.getElementById(id);
const snapshotExportButton = snapshotExportById("download-session-snapshot");
const snapshotExportStatus = snapshotExportById("snapshot-export-status");
const snapshotExportShaPattern = /^[0-9a-f]{64}$/;
const snapshotExportTerminalStates = new Set(["succeeded", "failed", "cancelled"]);

function snapshotExportSessionId() {
  return snapshotExportById("session-id").textContent.trim();
}

function snapshotExportSessionStatus() {
  return snapshotExportById("session-status").textContent.trim();
}

function snapshotExportIsCurrent(sessionId, generation) {
  return (
    snapshotExportState.generation === generation
    && snapshotExportSessionId() === sessionId
  );
}

function cancelSnapshotExport() {
  if (snapshotExportState.controller) snapshotExportState.controller.abort();
  snapshotExportState.controller = null;
}

function updateSnapshotExportAvailability() {
  snapshotExportButton.disabled = (
    !snapshotExportState.token
    || !snapshotExportSessionId()
    || !snapshotExportTerminalStates.has(snapshotExportSessionStatus())
  );
}

function snapshotExportMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

async function snapshotExportResponseError(response) {
  const fallback = `HTTP ${response.status}`;
  try {
    const payload = await response.json();
    return payload.detail || payload.error || fallback;
  } catch (_error) {
    return fallback;
  }
}

async function snapshotExportSha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(
    new Uint8Array(digest),
    (item) => item.toString(16).padStart(2, "0"),
  ).join("");
}

async function downloadSessionSnapshot() {
  const sessionId = snapshotExportSessionId();
  if (
    !snapshotExportState.token
    || !sessionId
    || !snapshotExportTerminalStates.has(snapshotExportSessionStatus())
  ) return;

  const generation = snapshotExportState.generation;
  cancelSnapshotExport();
  const controller = new AbortController();
  snapshotExportState.controller = controller;
  snapshotExportButton.disabled = true;
  snapshotExportStatus.textContent = "Preparing authenticated session snapshot…";

  try {
    const response = await fetch(
      `/v1/sessions/${encodeURIComponent(sessionId)}/snapshot/export`,
      {
        method: "GET",
        headers: {
          Authorization: `Bearer ${snapshotExportState.token}`,
          Accept: "application/json",
        },
        cache: "no-store",
        credentials: "omit",
        signal: controller.signal,
      },
    );
    if (!snapshotExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    if (response.status === 401) {
      snapshotExportState.token = null;
      throw new Error("operator token was rejected; unlock the operator view again");
    }
    if (!response.ok) throw new Error(await snapshotExportResponseError(response));
    if (!snapshotExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;

    if ((response.headers.get("Content-Type") || "").toLowerCase() !== "application/json; charset=utf-8") {
      throw new Error("session snapshot returned an unexpected content type");
    }
    if (
      response.headers.get("Content-Disposition")
      !== 'attachment; filename="session-snapshot.json"'
    ) {
      throw new Error("session snapshot returned an unexpected filename");
    }

    const sourceSha256 = response.headers.get("X-Harness-X-Snapshot-SHA256") || "";
    const headerFingerprint = response.headers.get("X-Harness-X-Snapshot-Fingerprint") || "";
    const rawRevision = response.headers.get("X-Harness-X-Snapshot-Revision");
    const rawLength = response.headers.get("Content-Length");
    if (!snapshotExportShaPattern.test(sourceSha256)) {
      throw new Error("session snapshot returned an invalid response SHA-256");
    }
    if (!snapshotExportShaPattern.test(headerFingerprint)) {
      throw new Error("session snapshot returned an invalid fingerprint header");
    }
    if (rawRevision == null || !/^[1-9][0-9]*$/.test(rawRevision)) {
      throw new Error("session snapshot returned an invalid revision header");
    }
    if (rawLength == null || !/^(0|[1-9][0-9]*)$/.test(rawLength)) {
      throw new Error("session snapshot returned an invalid content length");
    }

    const bytes = await response.arrayBuffer();
    if (!snapshotExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    const declaredLength = Number.parseInt(rawLength, 10);
    if (!Number.isSafeInteger(declaredLength) || bytes.byteLength !== declaredLength) {
      throw new Error("session snapshot byte count does not match Content-Length");
    }
    const observedSha256 = await snapshotExportSha256Hex(bytes);
    if (!snapshotExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    if (observedSha256 !== sourceSha256) {
      throw new Error("session snapshot bytes do not match the declared SHA-256");
    }

    let snapshot;
    try {
      snapshot = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    } catch (_error) {
      throw new Error("session snapshot body is not valid UTF-8 JSON");
    }
    const declaredRevision = Number.parseInt(rawRevision, 10);
    if (snapshot.schema_version !== "app-session-snapshot-v1") {
      throw new Error("session snapshot returned an unexpected schema version");
    }
    if (snapshot.request?.schema_version !== "app-coding-session-request-v1") {
      throw new Error("session snapshot returned an unexpected request schema version");
    }
    if (snapshot.session_id !== sessionId) {
      throw new Error("session snapshot belongs to a different session");
    }
    if (!snapshotExportTerminalStates.has(snapshot.status)) {
      throw new Error("session snapshot returned a nonterminal status");
    }
    if (snapshot.fingerprint !== headerFingerprint) {
      throw new Error("session snapshot fingerprint does not match the response header");
    }
    if (
      !Number.isSafeInteger(snapshot.revision)
      || snapshot.revision !== declaredRevision
    ) {
      throw new Error("session snapshot revision does not match the response header");
    }

    const blob = new Blob([bytes], { type: "application/json;charset=utf-8" });
    const objectUrl = URL.createObjectURL(blob);
    try {
      if (!snapshotExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = "session-snapshot.json";
      document.body.append(link);
      link.click();
      link.remove();
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
    if (!snapshotExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    snapshotExportStatus.textContent = (
      `Downloaded session snapshot · revision ${snapshot.revision} · sha256 ${sourceSha256}`
    );
  } catch (error) {
    if (!snapshotExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    snapshotExportStatus.textContent = (
      `Session snapshot download failed: ${snapshotExportMessage(error)}`
    );
  } finally {
    if (snapshotExportState.controller === controller) snapshotExportState.controller = null;
    updateSnapshotExportAvailability();
  }
}

snapshotExportById("auth-form").addEventListener("submit", () => {
  const token = snapshotExportById("token").value.trim();
  if (token) snapshotExportState.token = token;
  updateSnapshotExportAvailability();
});

snapshotExportById("lock-button").addEventListener("click", () => {
  snapshotExportState.generation += 1;
  cancelSnapshotExport();
  snapshotExportState.token = null;
  snapshotExportStatus.textContent = "";
  updateSnapshotExportAvailability();
});

new MutationObserver(() => {
  snapshotExportState.generation += 1;
  cancelSnapshotExport();
  snapshotExportStatus.textContent = "";
  updateSnapshotExportAvailability();
}).observe(snapshotExportById("session-id"), {
  childList: true,
  characterData: true,
  subtree: true,
});

new MutationObserver(updateSnapshotExportAvailability).observe(
  snapshotExportById("session-status"),
  {
    childList: true,
    characterData: true,
    subtree: true,
  },
);

new MutationObserver(() => {
  if (snapshotExportById("auth-state").textContent.trim() === "Locked") {
    snapshotExportState.generation += 1;
    cancelSnapshotExport();
    snapshotExportState.token = null;
    snapshotExportStatus.textContent = "";
  }
  updateSnapshotExportAvailability();
}).observe(snapshotExportById("auth-state"), {
  childList: true,
  characterData: true,
  subtree: true,
});

snapshotExportButton.addEventListener("click", () => {
  void downloadSessionSnapshot();
});

window.addEventListener("beforeunload", cancelSnapshotExport);
updateSnapshotExportAvailability();