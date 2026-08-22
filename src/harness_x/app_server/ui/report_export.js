"use strict";

const reportExportState = {
  token: null,
};

const reportExportById = (id) => document.getElementById(id);
const reportExportButton = reportExportById("download-report");
const reportExportStatus = reportExportById("report-export-status");
const reportExportShaPattern = /^[0-9a-f]{64}$/;
const reportExportAttestationStates = new Set([
  "verified",
  "legacy_unattested",
  "unavailable",
]);

function reportExportSessionId() {
  return reportExportById("session-id").textContent.trim();
}

function updateReportExportAvailability() {
  reportExportButton.disabled = !reportExportState.token || !reportExportSessionId();
}

function reportExportMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

async function responseError(response) {
  let fallback = `HTTP ${response.status}`;
  try {
    const payload = await response.json();
    return payload.detail || payload.error || fallback;
  } catch (_error) {
    return fallback;
  }
}

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (item) => item.toString(16).padStart(2, "0")).join("");
}

async function downloadExactCodingReport() {
  const sessionId = reportExportSessionId();
  if (!reportExportState.token || !sessionId) return;

  reportExportButton.disabled = true;
  reportExportStatus.textContent = "Preparing authenticated report export…";
  try {
    const response = await fetch(
      `/v1/sessions/${encodeURIComponent(sessionId)}/report/export`,
      {
        method: "GET",
        headers: {
          Authorization: `Bearer ${reportExportState.token}`,
          Accept: "application/json",
        },
        cache: "no-store",
        credentials: "omit",
      },
    );
    if (response.status === 401) {
      reportExportState.token = null;
      throw new Error("operator token was rejected; unlock the operator view again");
    }
    if (!response.ok) throw new Error(await responseError(response));

    const contentType = response.headers.get("Content-Type") || "";
    if (contentType.toLowerCase() !== "application/json; charset=utf-8") {
      throw new Error("report export returned an unexpected content type");
    }
    if (
      response.headers.get("Content-Disposition")
      !== 'attachment; filename="coding-task-report.json"'
    ) {
      throw new Error("report export returned an unexpected filename");
    }

    const attestation = response.headers.get("X-Harness-X-Report-Attestation") || "";
    const sourceSha256 = response.headers.get("X-Harness-X-Report-SHA256") || "";
    const artifactEventHash = response.headers.get("X-Harness-X-Artifact-Event-Hash") || "";
    const rawLength = response.headers.get("Content-Length");
    if (!reportExportAttestationStates.has(attestation)) {
      throw new Error("report export returned an unknown attestation state");
    }
    if (!reportExportShaPattern.test(sourceSha256)) {
      throw new Error("report export returned an invalid source SHA-256");
    }
    if (!reportExportShaPattern.test(artifactEventHash)) {
      throw new Error("report export returned an invalid artifact event hash");
    }
    if (rawLength == null || !/^(0|[1-9][0-9]*)$/.test(rawLength)) {
      throw new Error("report export returned an invalid content length");
    }

    const bytes = await response.arrayBuffer();
    const declaredLength = Number.parseInt(rawLength, 10);
    if (!Number.isSafeInteger(declaredLength) || bytes.byteLength !== declaredLength) {
      throw new Error("report export byte count does not match Content-Length");
    }
    const observedSha256 = await sha256Hex(bytes);
    if (observedSha256 !== sourceSha256) {
      throw new Error("report export bytes do not match the declared SHA-256");
    }

    const blob = new Blob([bytes], { type: "application/json;charset=utf-8" });
    const objectUrl = URL.createObjectURL(blob);
    try {
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = "coding-task-report.json";
      document.body.append(link);
      link.click();
      link.remove();
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
    reportExportStatus.textContent = `Downloaded ${declaredLength} bytes · ${attestation} · sha256 ${sourceSha256}`;
  } catch (error) {
    reportExportStatus.textContent = `Report download failed: ${reportExportMessage(error)}`;
  } finally {
    updateReportExportAvailability();
  }
}

reportExportById("auth-form").addEventListener("submit", () => {
  const token = reportExportById("token").value.trim();
  if (token) reportExportState.token = token;
  updateReportExportAvailability();
});

reportExportById("lock-button").addEventListener("click", () => {
  reportExportState.token = null;
  reportExportStatus.textContent = "";
  updateReportExportAvailability();
});

new MutationObserver(updateReportExportAvailability).observe(reportExportById("session-id"), {
  childList: true,
  characterData: true,
  subtree: true,
});

new MutationObserver(() => {
  if (reportExportById("auth-state").textContent.trim() === "Locked") {
    reportExportState.token = null;
    reportExportStatus.textContent = "";
  }
  updateReportExportAvailability();
}).observe(reportExportById("auth-state"), {
  childList: true,
  characterData: true,
  subtree: true,
});

reportExportButton.addEventListener("click", () => {
  void downloadExactCodingReport();
});

updateReportExportAvailability();
