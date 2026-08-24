"use strict";

const signedManifestPairState = {
  token: null,
  generation: 0,
  controller: null,
};

const signedManifestPairById = (id) => document.getElementById(id);
const signedManifestPairButton = signedManifestPairById("download-signed-manifest-pair");
const signedManifestPairStatus = signedManifestPairById("signed-manifest-pair-status");
const signedManifestPairShaPattern = /^[0-9a-f]{64}$/;
const signedManifestPairKeyPattern = /^sha256:[0-9a-f]{64}$/;
const signedManifestPairSignaturePattern = /^[A-Za-z0-9_-]{85}[AEIMQUYcgkosw048]$/;
const signedManifestPairTerminalStates = new Set(["succeeded", "failed", "cancelled"]);
const signedManifestPairAvailabilityStates = new Set(["available", "not_available"]);
const signedManifestPairEnvelopeKeys = [
  "algorithm",
  "key_fingerprint",
  "manifest_sha256",
  "schema_version",
  "signature",
];

function signedManifestPairSessionId() {
  return signedManifestPairById("session-id").textContent.trim();
}

function signedManifestPairSessionStatus() {
  return signedManifestPairById("session-status").textContent.trim();
}

function signedManifestPairIsCurrent(sessionId, generation) {
  return (
    signedManifestPairState.generation === generation
    && signedManifestPairSessionId() === sessionId
  );
}

function cancelSignedManifestPairDownload() {
  if (signedManifestPairState.controller) signedManifestPairState.controller.abort();
  signedManifestPairState.controller = null;
}

function updateSignedManifestPairAvailability() {
  signedManifestPairButton.disabled = (
    !signedManifestPairState.token
    || !signedManifestPairSessionId()
    || !signedManifestPairTerminalStates.has(signedManifestPairSessionStatus())
  );
}

function signedManifestPairMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

async function signedManifestPairResponseError(response) {
  const fallback = `HTTP ${response.status}`;
  try {
    const payload = await response.json();
    return payload.detail || payload.error || fallback;
  } catch (_error) {
    return fallback;
  }
}

async function signedManifestPairSha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(
    new Uint8Array(digest),
    (item) => item.toString(16).padStart(2, "0"),
  ).join("");
}

function signedManifestPairLength(response, label) {
  const rawLength = response.headers.get("Content-Length");
  if (rawLength == null || !/^(0|[1-9][0-9]*)$/.test(rawLength)) {
    throw new Error(`${label} returned an invalid content length`);
  }
  const declaredLength = Number.parseInt(rawLength, 10);
  if (!Number.isSafeInteger(declaredLength)) {
    throw new Error(`${label} returned an unsafe content length`);
  }
  return declaredLength;
}

function signedManifestPairText(bytes, label) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch (_error) {
    throw new Error(`${label} body is not valid UTF-8`);
  }
}

function signedManifestPairJson(bytes, label) {
  const text = signedManifestPairText(bytes, label);
  try {
    return { value: JSON.parse(text), text };
  } catch (_error) {
    throw new Error(`${label} body is not valid JSON`);
  }
}

function signedManifestPairHasExactKeys(value, expected) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const observed = Object.keys(value).sort();
  if (observed.length !== expected.length) return false;
  return observed.every((key, index) => key === expected[index]);
}

function signedManifestPairCanonicalEnvelope(envelope) {
  return JSON.stringify({
    algorithm: envelope.algorithm,
    key_fingerprint: envelope.key_fingerprint,
    manifest_sha256: envelope.manifest_sha256,
    schema_version: envelope.schema_version,
    signature: envelope.signature,
  }) + "\n";
}

async function signedManifestPairFetchManifest(sessionId, token, generation, controller) {
  const response = await fetch(
    `/v1/sessions/${encodeURIComponent(sessionId)}/evidence/manifest`,
    {
      method: "GET",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
      },
      cache: "no-store",
      credentials: "omit",
      signal: controller.signal,
    },
  );
  if (!signedManifestPairIsCurrent(sessionId, generation) || controller.signal.aborted) return null;
  if (response.status === 401) {
    signedManifestPairState.token = null;
    throw new Error("operator token was rejected; unlock the operator view again");
  }
  if (!response.ok) throw new Error(await signedManifestPairResponseError(response));
  if (!signedManifestPairIsCurrent(sessionId, generation) || controller.signal.aborted) return null;

  if ((response.headers.get("Content-Type") || "").toLowerCase() !== "application/json; charset=utf-8") {
    throw new Error("evidence manifest returned an unexpected content type");
  }
  if (
    response.headers.get("Content-Disposition")
    !== 'attachment; filename="session-evidence-manifest.json"'
  ) {
    throw new Error("evidence manifest returned an unexpected filename");
  }
  const sourceSha256 = response.headers.get("X-Harness-X-Evidence-Manifest-SHA256") || "";
  if (!signedManifestPairShaPattern.test(sourceSha256)) {
    throw new Error("evidence manifest returned an invalid response SHA-256");
  }
  const declaredLength = signedManifestPairLength(response, "evidence manifest");
  const bytes = await response.arrayBuffer();
  if (!signedManifestPairIsCurrent(sessionId, generation) || controller.signal.aborted) return null;
  if (bytes.byteLength !== declaredLength) {
    throw new Error("evidence manifest byte count does not match Content-Length");
  }
  const observedSha256 = await signedManifestPairSha256Hex(bytes);
  if (!signedManifestPairIsCurrent(sessionId, generation) || controller.signal.aborted) return null;
  if (observedSha256 !== sourceSha256) {
    throw new Error("evidence manifest bytes do not match the declared SHA-256");
  }

  const { value: manifest } = signedManifestPairJson(bytes, "evidence manifest");
  if (manifest.schema_version !== "app-terminal-evidence-manifest-v1") {
    throw new Error("evidence manifest returned an unexpected schema version");
  }
  if (manifest.session_id !== sessionId) {
    throw new Error("evidence manifest belongs to a different session");
  }
  if (!signedManifestPairShaPattern.test(manifest.fingerprint || "")) {
    throw new Error("evidence manifest returned an invalid self-fingerprint");
  }
  if (
    !manifest.lifecycle
    || !signedManifestPairTerminalStates.has(manifest.lifecycle.status)
    || !signedManifestPairShaPattern.test(manifest.lifecycle.ledger_head_hash || "")
  ) {
    throw new Error("evidence manifest returned invalid lifecycle evidence");
  }
  if (
    !manifest.coding_report
    || !signedManifestPairAvailabilityStates.has(manifest.coding_report.availability)
    || !manifest.causal_trace
    || !signedManifestPairAvailabilityStates.has(manifest.causal_trace.availability)
  ) {
    throw new Error("evidence manifest returned invalid component availability");
  }
  return { bytes, sourceSha256 };
}

async function signedManifestPairFetchSignature(
  sessionId,
  token,
  generation,
  controller,
  manifestSha256,
) {
  const response = await fetch(
    `/v1/sessions/${encodeURIComponent(sessionId)}/evidence/signature`,
    {
      method: "GET",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
      },
      cache: "no-store",
      credentials: "omit",
      signal: controller.signal,
    },
  );
  if (!signedManifestPairIsCurrent(sessionId, generation) || controller.signal.aborted) return null;
  if (response.status === 401) {
    signedManifestPairState.token = null;
    throw new Error("operator token was rejected; unlock the operator view again");
  }
  if (!response.ok) throw new Error(await signedManifestPairResponseError(response));
  if (!signedManifestPairIsCurrent(sessionId, generation) || controller.signal.aborted) return null;

  if ((response.headers.get("Content-Type") || "").toLowerCase() !== "application/json; charset=utf-8") {
    throw new Error("evidence signature returned an unexpected content type");
  }
  if (
    response.headers.get("Content-Disposition")
    !== 'attachment; filename="session-evidence-manifest.sig.json"'
  ) {
    throw new Error("evidence signature returned an unexpected filename");
  }
  const responseManifestSha = response.headers.get("X-Harness-X-Evidence-Manifest-SHA256") || "";
  if (!signedManifestPairShaPattern.test(responseManifestSha)) {
    throw new Error("evidence signature returned an invalid manifest SHA-256 header");
  }
  if (responseManifestSha !== manifestSha256) {
    throw new Error("evidence signature refers to different manifest bytes");
  }
  const keyFingerprint = response.headers.get("X-Harness-X-Evidence-Signature-Key") || "";
  if (!signedManifestPairKeyPattern.test(keyFingerprint)) {
    throw new Error("evidence signature returned an invalid key fingerprint");
  }
  if (response.headers.get("X-Harness-X-Evidence-Signature-Algorithm") !== "ed25519") {
    throw new Error("evidence signature returned an unexpected algorithm");
  }

  const declaredLength = signedManifestPairLength(response, "evidence signature");
  const bytes = await response.arrayBuffer();
  if (!signedManifestPairIsCurrent(sessionId, generation) || controller.signal.aborted) return null;
  if (bytes.byteLength !== declaredLength) {
    throw new Error("evidence signature byte count does not match Content-Length");
  }

  const { value: envelope, text: envelopeText } = signedManifestPairJson(bytes, "evidence signature");
  if (!signedManifestPairHasExactKeys(envelope, signedManifestPairEnvelopeKeys)) {
    throw new Error("evidence signature returned unexpected envelope fields");
  }
  if (envelope.schema_version !== "app-evidence-signature-v1") {
    throw new Error("evidence signature returned an unexpected schema version");
  }
  if (envelope.algorithm !== "ed25519") {
    throw new Error("evidence signature envelope returned an unexpected algorithm");
  }
  if (envelope.key_fingerprint !== keyFingerprint) {
    throw new Error("evidence signature key fingerprint does not match its response header");
  }
  if (envelope.manifest_sha256 !== manifestSha256) {
    throw new Error("evidence signature envelope refers to different manifest bytes");
  }
  if (!signedManifestPairSignaturePattern.test(envelope.signature || "")) {
    throw new Error("evidence signature envelope contains non-canonical Ed25519 signature text");
  }
  if (envelopeText !== signedManifestPairCanonicalEnvelope(envelope)) {
    throw new Error("evidence signature body is not the canonical M52 envelope serialization");
  }
  return { bytes, keyFingerprint };
}

function signedManifestPairClickDownload(bytes, type, filename) {
  const blob = new Blob([bytes], { type });
  const objectUrl = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

async function downloadSignedManifestPair() {
  const sessionId = signedManifestPairSessionId();
  const token = signedManifestPairState.token;
  if (
    !token
    || !sessionId
    || !signedManifestPairTerminalStates.has(signedManifestPairSessionStatus())
  ) return;

  const generation = signedManifestPairState.generation;
  cancelSignedManifestPairDownload();
  const controller = new AbortController();
  signedManifestPairState.controller = controller;
  signedManifestPairButton.disabled = true;
  signedManifestPairStatus.textContent = "Preparing correlated manifest and signature…";

  try {
    const manifest = await signedManifestPairFetchManifest(
      sessionId,
      token,
      generation,
      controller,
    );
    if (!manifest) return;
    const signature = await signedManifestPairFetchSignature(
      sessionId,
      token,
      generation,
      controller,
      manifest.sourceSha256,
    );
    if (!signature) return;
    if (!signedManifestPairIsCurrent(sessionId, generation) || controller.signal.aborted) return;

    signedManifestPairClickDownload(
      manifest.bytes,
      "application/json;charset=utf-8",
      "session-evidence-manifest.json",
    );
    signedManifestPairClickDownload(
      signature.bytes,
      "application/json;charset=utf-8",
      "session-evidence-manifest.sig.json",
    );
    signedManifestPairStatus.textContent = (
      `Signed pair download initiated · sha256 ${manifest.sourceSha256}`
      + ` · key ${signature.keyFingerprint}`
    );
  } catch (error) {
    if (!signedManifestPairIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    signedManifestPairStatus.textContent = (
      `Signed manifest pair download failed: ${signedManifestPairMessage(error)}`
    );
  } finally {
    if (signedManifestPairState.controller === controller) signedManifestPairState.controller = null;
    updateSignedManifestPairAvailability();
  }
}

signedManifestPairById("auth-form").addEventListener("submit", () => {
  const token = signedManifestPairById("token").value.trim();
  if (token) signedManifestPairState.token = token;
  updateSignedManifestPairAvailability();
});

signedManifestPairById("lock-button").addEventListener("click", () => {
  signedManifestPairState.generation += 1;
  cancelSignedManifestPairDownload();
  signedManifestPairState.token = null;
  signedManifestPairStatus.textContent = "";
  updateSignedManifestPairAvailability();
});

new MutationObserver(() => {
  signedManifestPairState.generation += 1;
  cancelSignedManifestPairDownload();
  signedManifestPairStatus.textContent = "";
  updateSignedManifestPairAvailability();
}).observe(signedManifestPairById("session-id"), {
  childList: true,
  characterData: true,
  subtree: true,
});

new MutationObserver(updateSignedManifestPairAvailability).observe(
  signedManifestPairById("session-status"),
  {
    childList: true,
    characterData: true,
    subtree: true,
  },
);

new MutationObserver(() => {
  if (signedManifestPairById("auth-state").textContent.trim() === "Locked") {
    signedManifestPairState.generation += 1;
    cancelSignedManifestPairDownload();
    signedManifestPairState.token = null;
    signedManifestPairStatus.textContent = "";
  }
  updateSignedManifestPairAvailability();
}).observe(signedManifestPairById("auth-state"), {
  childList: true,
  characterData: true,
  subtree: true,
});

signedManifestPairButton.addEventListener("click", () => {
  void downloadSignedManifestPair();
});

window.addEventListener("beforeunload", cancelSignedManifestPairDownload);
updateSignedManifestPairAvailability();
