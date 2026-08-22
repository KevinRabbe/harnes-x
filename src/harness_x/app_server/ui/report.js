"use strict";

const reportState = {
  token: null,
  generation: 0,
};

const reportById = (id) => document.getElementById(id);

function clearReport(message = "No durable coding report loaded.") {
  reportById("report-metadata").textContent = "—";
  reportById("report-content").textContent = message;
}

function currentSessionId() {
  return reportById("session-id").textContent.trim();
}

function attestationLabel(status) {
  if (status === "verified") return "ledger attestation verified";
  if (status === "legacy_unattested") return "legacy path-only artifact";
  if (status === "unavailable") return "attestation unavailable";
  return null;
}

async function loadCodingReport(sessionId) {
  const generation = reportState.generation + 1;
  reportState.generation = generation;
  if (!reportState.token || !sessionId) {
    clearReport();
    return;
  }

  reportById("report-metadata").textContent = "Loading…";
  reportById("report-content").textContent = "Loading authenticated coding report…";
  let response;
  try {
    response = await fetch(`/v1/sessions/${encodeURIComponent(sessionId)}/report`, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${reportState.token}`,
        Accept: "application/json",
      },
      cache: "no-store",
    });
  } catch (error) {
    if (generation !== reportState.generation || currentSessionId() !== sessionId) return;
    reportById("report-metadata").textContent = "Transport error";
    reportById("report-content").textContent = error instanceof Error ? error.message : String(error);
    return;
  }

  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = null;
  }
  if (generation !== reportState.generation || currentSessionId() !== sessionId) return;

  if (response.status === 401) {
    reportState.token = null;
    clearReport("Operator token was rejected; unlock the operator view again.");
    return;
  }
  if (response.status === 404 && payload && payload.error === "report_not_available") {
    clearReport("This session does not have a durable coding report yet.");
    return;
  }
  if (response.status === 409 && payload && payload.error === "report_corruption") {
    reportById("report-metadata").textContent = "Integrity failure";
    reportById("report-content").textContent = payload.detail || "Coding report projection failed integrity checks.";
    return;
  }

  const provenance = payload ? attestationLabel(payload.attestation_status) : null;
  if (
    !response.ok
    || !payload
    || payload.schema_version !== "app-coding-report-projection-v2"
    || !provenance
  ) {
    reportById("report-metadata").textContent = `HTTP ${response.status}`;
    reportById("report-content").textContent = payload && (payload.detail || payload.error)
      ? String(payload.detail || payload.error)
      : "Coding report response was not usable.";
    return;
  }

  const metadata = [
    provenance,
    `${payload.source_bytes} bytes`,
    `artifact event #${payload.artifact_event_sequence}`,
    `event ${payload.artifact_event_hash}`,
    `current sha256 ${payload.source_sha256}`,
  ];
  if (payload.attestation_status === "verified") {
    metadata.push(`attested sha256 ${payload.attested_source_sha256}`);
  } else if (payload.attestation_status === "unavailable" && payload.attestation_error) {
    metadata.push(`capture error: ${payload.attestation_error}`);
  }
  reportById("report-metadata").textContent = metadata.join(" · ");
  reportById("report-content").textContent = JSON.stringify(payload.report, null, 2);
}

reportById("auth-form").addEventListener("submit", () => {
  const token = reportById("token").value.trim();
  if (token) reportState.token = token;
});

reportById("lock-button").addEventListener("click", () => {
  reportState.token = null;
  reportState.generation += 1;
  clearReport();
});

const reloadSelectedReport = () => {
  void loadCodingReport(currentSessionId());
};

new MutationObserver(reloadSelectedReport).observe(reportById("session-id"), {
  childList: true,
  characterData: true,
  subtree: true,
});

new MutationObserver(reloadSelectedReport).observe(reportById("session-status"), {
  childList: true,
  characterData: true,
  subtree: true,
});

new MutationObserver(() => {
  if (reportById("auth-state").textContent.trim() === "Locked") {
    reportState.token = null;
    reportState.generation += 1;
    clearReport();
  }
}).observe(reportById("auth-state"), { childList: true, characterData: true, subtree: true });

clearReport();
