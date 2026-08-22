"use strict";

const traceExportState = {
  token: null,
  generation: 0,
  controller: null,
};

const traceExportById = (id) => document.getElementById(id);
const traceExportButton = traceExportById("download-trace");
const traceExportStatus = traceExportById("trace-export-status");
const traceExportShaPattern = /^[0-9a-f]{64}$/;
const traceExportIdPattern = /^trace_[0-9a-f]{32}$/;
const traceExportTerminalStates = new Set(["succeeded", "failed", "cancelled"]);

function traceExportSessionId() {
  return traceExportById("session-id").textContent.trim();
}

function traceExportSessionStatus() {
  return traceExportById("session-status").textContent.trim();
}

function traceExportIsCurrent(sessionId, generation) {
  return (
    traceExportState.generation === generation
    && traceExportSessionId() === sessionId
  );
}

function cancelTraceExport() {
  if (traceExportState.controller) traceExportState.controller.abort();
  traceExportState.controller = null;
}

function updateTraceExportAvailability() {
  traceExportButton.disabled = (
    !traceExportState.token
    || !traceExportSessionId()
    || !traceExportTerminalStates.has(traceExportSessionStatus())
  );
}

function traceExportMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

async function traceExportResponseError(response) {
  const fallback = `HTTP ${response.status}`;
  try {
    const payload = await response.json();
    return payload.detail || payload.error || fallback;
  } catch (_error) {
    return fallback;
  }
}

async function traceExportSha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(
    new Uint8Array(digest),
    (item) => item.toString(16).padStart(2, "0"),
  ).join("");
}

async function downloadVerifiedTrace() {
  const sessionId = traceExportSessionId();
  if (
    !traceExportState.token
    || !sessionId
    || !traceExportTerminalStates.has(traceExportSessionStatus())
  ) return;

  const generation = traceExportState.generation;
  cancelTraceExport();
  const controller = new AbortController();
  traceExportState.controller = controller;

  traceExportButton.disabled = true;
  traceExportStatus.textContent = "Preparing authenticated verified trace export…";
  try {
    const response = await fetch(
      `/v1/sessions/${encodeURIComponent(sessionId)}/trace/export`,
      {
        method: "GET",
        headers: {
          Authorization: `Bearer ${traceExportState.token}`,
          Accept: "application/x-ndjson",
        },
        cache: "no-store",
        credentials: "omit",
        signal: controller.signal,
      },
    );
    if (!traceExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    if (response.status === 401) {
      traceExportState.token = null;
      throw new Error("operator token was rejected; unlock the operator view again");
    }
    if (!response.ok) throw new Error(await traceExportResponseError(response));
    if (!traceExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;

    const contentType = response.headers.get("Content-Type") || "";
    if (contentType.toLowerCase() !== "application/x-ndjson; charset=utf-8") {
      throw new Error("trace export returned an unexpected content type");
    }
    if (
      response.headers.get("Content-Disposition")
      !== 'attachment; filename="causal-trace.jsonl"'
    ) {
      throw new Error("trace export returned an unexpected filename");
    }

    const traceId = response.headers.get("X-Harness-X-Trace-ID") || "";
    const sourceSha256 = response.headers.get("X-Harness-X-Trace-SHA256") || "";
    const rawRecords = response.headers.get("X-Harness-X-Trace-Records");
    const finalEventHash = response.headers.get("X-Harness-X-Trace-Final-Event-Hash") || "";
    const attachmentEventHash = (
      response.headers.get("X-Harness-X-Trace-Attachment-Event-Hash") || ""
    );
    const rawLength = response.headers.get("Content-Length");

    if (!traceExportIdPattern.test(traceId)) {
      throw new Error("trace export returned an invalid trace id");
    }
    if (!traceExportShaPattern.test(sourceSha256)) {
      throw new Error("trace export returned an invalid source SHA-256");
    }
    if (!traceExportShaPattern.test(attachmentEventHash)) {
      throw new Error("trace export returned an invalid attachment event hash");
    }
    if (rawRecords == null || !/^(0|[1-9][0-9]*)$/.test(rawRecords)) {
      throw new Error("trace export returned an invalid record count");
    }
    if (rawLength == null || !/^(0|[1-9][0-9]*)$/.test(rawLength)) {
      throw new Error("trace export returned an invalid content length");
    }

    const recordCount = Number.parseInt(rawRecords, 10);
    if (!Number.isSafeInteger(recordCount)) {
      throw new Error("trace export record count is outside the safe integer range");
    }
    if (
      (recordCount === 0 && finalEventHash !== "none")
      || (recordCount > 0 && !traceExportShaPattern.test(finalEventHash))
    ) {
      throw new Error("trace export returned an invalid final event hash");
    }

    const bytes = await response.arrayBuffer();
    if (!traceExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    const declaredLength = Number.parseInt(rawLength, 10);
    if (!Number.isSafeInteger(declaredLength) || bytes.byteLength !== declaredLength) {
      throw new Error("trace export byte count does not match Content-Length");
    }
    const observedSha256 = await traceExportSha256Hex(bytes);
    if (!traceExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    if (observedSha256 !== sourceSha256) {
      throw new Error("trace export bytes do not match the declared SHA-256");
    }

    const blob = new Blob([bytes], { type: "application/x-ndjson;charset=utf-8" });
    const objectUrl = URL.createObjectURL(blob);
    try {
      if (!traceExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = "causal-trace.jsonl";
      document.body.append(link);
      link.click();
      link.remove();
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
    if (!traceExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    traceExportStatus.textContent = (
      `Downloaded ${declaredLength} bytes · ${recordCount} records · sha256 ${sourceSha256}`
    );
  } catch (error) {
    if (!traceExportIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    traceExportStatus.textContent = `Trace download failed: ${traceExportMessage(error)}`;
  } finally {
    if (traceExportState.controller === controller) traceExportState.controller = null;
    updateTraceExportAvailability();
  }
}

traceExportById("auth-form").addEventListener("submit", () => {
  const token = traceExportById("token").value.trim();
  if (token) traceExportState.token = token;
  updateTraceExportAvailability();
});

traceExportById("lock-button").addEventListener("click", () => {
  traceExportState.generation += 1;
  cancelTraceExport();
  traceExportState.token = null;
  traceExportStatus.textContent = "";
  updateTraceExportAvailability();
});

new MutationObserver(() => {
  traceExportState.generation += 1;
  cancelTraceExport();
  traceExportStatus.textContent = "";
  updateTraceExportAvailability();
}).observe(traceExportById("session-id"), {
  childList: true,
  characterData: true,
  subtree: true,
});

new MutationObserver(updateTraceExportAvailability).observe(traceExportById("session-status"), {
  childList: true,
  characterData: true,
  subtree: true,
});

new MutationObserver(() => {
  if (traceExportById("auth-state").textContent.trim() === "Locked") {
    traceExportState.generation += 1;
    cancelTraceExport();
    traceExportState.token = null;
    traceExportStatus.textContent = "";
  }
  updateTraceExportAvailability();
}).observe(traceExportById("auth-state"), {
  childList: true,
  characterData: true,
  subtree: true,
});

traceExportButton.addEventListener("click", () => {
  void downloadVerifiedTrace();
});

window.addEventListener("beforeunload", cancelTraceExport);
updateTraceExportAvailability();
