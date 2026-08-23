"use strict";

const lifecycleExportState = {
  token: null,
  generation: 0,
  controller: null,
};

const lifecycleExportById = (id) => document.getElementById(id);
const lifecycleExportButton = lifecycleExportById("download-lifecycle-ledger");
const lifecycleExportStatus = lifecycleExportById("lifecycle-export-status");
const lifecycleExportShaPattern = /^[0-9a-f]{64}$/;
const lifecycleExportTerminalStates = new Set(["succeeded", "failed", "cancelled"]);

function lifecycleExportSessionId() {
  return lifecycleExportById("session-id").textContent.trim();
}

function lifecycleExportSessionStatus() {
  return lifecycleExportById("session-status").textContent.trim();
}

function lifecycleExportIsCurrent(sessionId, generation) {
  return (
    lifecycleExportState.generation === generation
    && lifecycleExportSessionId() === sessionId
  );
}

function cancelLifecycleExport() {
  if (lifecycleExportState.controller) lifecycleExportState.controller.abort();
  lifecycleExportState.controller = null;
}

function updateLifecycleExportAvailability() {
  lifecycleExportButton.disabled = (
    !lifecycleExportState.token
    || !lifecycleExportSessionId()
    || !lifecycleExportTerminalStates.has(lifecycleExportSessionStatus())
  );
}

function lifecycleExportMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

async function lifecycleExportResponseError(response) {
  const fallback = `HTTP ${response.status}`;
  try {
    const payload = await response.json();
    return payload.detail || payload.error || fallback;
  } catch (_error) {
    return fallback;
  }
}

async function lifecycleExportSha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(
    new Uint8Array(digest),
    (item) => item.toString(16).padStart(2, "0"),
  ).join("");
}

async function downloadLifecycleLedger() {
  const sessionId = lifecycleExportSessionId();
  if (
    !lifecycleExportState.token
    || !sessionId
    || !lifecycleExportTerminalStates.has(lifecycleExportSessionStatus())
  ) return;

  const generation = lifecycleExportState.generation;
  cancelLifecycleExport();
  const controller = new AbortController();
  lifecycleExportState.controller = controller;
  lifecycleExportButton.disabled = true;
  lifecycleExportStatus.textContent = "Preparing authenticated lifecycle ledger…";

  try {
    const response = await fetch(
      `/v1/sessions/${encodeURIComponent(sessionId)}/lifecycle/export`,
      {
        method: "GET",
        headers: {
          Authorization: `Bearer ${lifecycleExportState.token}`,
          Accept: "application/json",
        },
        cache: "no-store",
        credentials: "omit",
        signal: controller.signal,
      },
    );
    if (!lifecycleExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    if (response.status === 401) {
      lifecycleExportState.token = null;
      throw new Error("operator token was rejected; unlock the operator view again");
    }
    if (!response.ok) throw new Error(await lifecycleExportResponseError(response));
    if (!lifecycleExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;

    if ((response.headers.get("Content-Type") || "").toLowerCase() !== "application/json; charset=utf-8") {
      throw new Error("lifecycle ledger returned an unexpected content type");
    }
    if (
      response.headers.get("Content-Disposition")
      !== 'attachment; filename="session-lifecycle-ledger.json"'
    ) {
      throw new Error("lifecycle ledger returned an unexpected filename");
    }

    const sourceSha256 = response.headers.get("X-Harness-X-Lifecycle-SHA256") || "";
    const rawLength = response.headers.get("Content-Length");
    const rawEvents = response.headers.get("X-Harness-X-Lifecycle-Events");
    const headerHeadHash = response.headers.get("X-Harness-X-Lifecycle-Head-Hash") || "";
    if (!lifecycleExportShaPattern.test(sourceSha256)) {
      throw new Error("lifecycle ledger returned an invalid response SHA-256");
    }
    if (!lifecycleExportShaPattern.test(headerHeadHash)) {
      throw new Error("lifecycle ledger returned an invalid head hash");
    }
    if (rawLength == null || !/^(0|[1-9][0-9]*)$/.test(rawLength)) {
      throw new Error("lifecycle ledger returned an invalid content length");
    }
    if (rawEvents == null || !/^[1-9][0-9]*$/.test(rawEvents)) {
      throw new Error("lifecycle ledger returned an invalid event count");
    }

    const bytes = await response.arrayBuffer();
    if (!lifecycleExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    const declaredLength = Number.parseInt(rawLength, 10);
    if (!Number.isSafeInteger(declaredLength) || bytes.byteLength !== declaredLength) {
      throw new Error("lifecycle ledger byte count does not match Content-Length");
    }
    const observedSha256 = await lifecycleExportSha256Hex(bytes);
    if (!lifecycleExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    if (observedSha256 !== sourceSha256) {
      throw new Error("lifecycle ledger bytes do not match the declared SHA-256");
    }

    let ledger;
    try {
      ledger = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    } catch (_error) {
      throw new Error("lifecycle ledger body is not valid UTF-8 JSON");
    }
    const declaredEvents = Number.parseInt(rawEvents, 10);
    if (ledger.schema_version !== "app-lifecycle-ledger-export-v1") {
      throw new Error("lifecycle ledger returned an unexpected schema version");
    }
    if (ledger.session_id !== sessionId) {
      throw new Error("lifecycle ledger belongs to a different session");
    }
    if (!lifecycleExportTerminalStates.has(ledger.status)) {
      throw new Error("lifecycle ledger returned a nonterminal status");
    }
    if (
      !Number.isSafeInteger(ledger.event_count)
      || ledger.event_count < 1
      || ledger.event_count !== declaredEvents
      || !Array.isArray(ledger.events)
      || ledger.events.length !== ledger.event_count
    ) {
      throw new Error("lifecycle ledger returned inconsistent event count metadata");
    }
    if (
      ledger.ledger_head_hash !== headerHeadHash
      || !lifecycleExportShaPattern.test(ledger.ledger_head_hash || "")
      || ledger.events[ledger.events.length - 1]?.event_hash !== ledger.ledger_head_hash
    ) {
      throw new Error("lifecycle ledger returned inconsistent head metadata");
    }

    const blob = new Blob([bytes], { type: "application/json;charset=utf-8" });
    const objectUrl = URL.createObjectURL(blob);
    try {
      if (!lifecycleExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = "session-lifecycle-ledger.json";
      document.body.append(link);
      link.click();
      link.remove();
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
    if (!lifecycleExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    lifecycleExportStatus.textContent = (
      `Downloaded lifecycle ledger · ${ledger.event_count} events · sha256 ${sourceSha256}`
    );
  } catch (error) {
    if (!lifecycleExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    lifecycleExportStatus.textContent = (
      `Lifecycle ledger download failed: ${lifecycleExportMessage(error)}`
    );
  } finally {
    if (lifecycleExportState.controller === controller) lifecycleExportState.controller = null;
    updateLifecycleExportAvailability();
  }
}

lifecycleExportById("auth-form").addEventListener("submit", () => {
  const token = lifecycleExportById("token").value.trim();
  if (token) lifecycleExportState.token = token;
  updateLifecycleExportAvailability();
});

lifecycleExportById("lock-button").addEventListener("click", () => {
  lifecycleExportState.generation += 1;
  cancelLifecycleExport();
  lifecycleExportState.token = null;
  lifecycleExportStatus.textContent = "";
  updateLifecycleExportAvailability();
});

new MutationObserver(() => {
  lifecycleExportState.generation += 1;
  cancelLifecycleExport();
  lifecycleExportStatus.textContent = "";
  updateLifecycleExportAvailability();
}).observe(lifecycleExportById("session-id"), {
  childList: true,
  characterData: true,
  subtree: true,
});

new MutationObserver(updateLifecycleExportAvailability).observe(
  lifecycleExportById("session-status"),
  {
    childList: true,
    characterData: true,
    subtree: true,
  },
);

new MutationObserver(() => {
  if (lifecycleExportById("auth-state").textContent.trim() === "Locked") {
    lifecycleExportState.generation += 1;
    cancelLifecycleExport();
    lifecycleExportState.token = null;
    lifecycleExportStatus.textContent = "";
  }
  updateLifecycleExportAvailability();
}).observe(lifecycleExportById("auth-state"), {
  childList: true,
  characterData: true,
  subtree: true,
});

lifecycleExportButton.addEventListener("click", () => {
  void downloadLifecycleLedger();
});

window.addEventListener("beforeunload", cancelLifecycleExport);
updateLifecycleExportAvailability();
