"use strict";

const signedManifestCapsuleState = {
  token: null,
  generation: 0,
  controller: null,
};

const signedManifestCapsuleById = (id) => document.getElementById(id);
const signedManifestCapsuleButton = signedManifestCapsuleById("download-signed-manifest-capsule");
const signedManifestCapsuleStatus = signedManifestCapsuleById("signed-manifest-capsule-status");
const signedManifestCapsuleKeys = [
  "algorithm",
  "key_fingerprint",
  "manifest_payload",
  "manifest_sha256",
  "schema_version",
  "signature_payload",
];
const signedManifestCapsuleBase64urlPattern = /^[A-Za-z0-9_-]*$/;

function signedManifestCapsuleSessionId() {
  return signedManifestCapsuleById("session-id").textContent.trim();
}

function signedManifestCapsuleSessionStatus() {
  return signedManifestCapsuleById("session-status").textContent.trim();
}

function signedManifestCapsuleIsCurrent(sessionId, generation) {
  return (
    signedManifestCapsuleState.generation === generation
    && signedManifestCapsuleSessionId() === sessionId
  );
}

function cancelSignedManifestCapsuleDownload() {
  if (signedManifestCapsuleState.controller) signedManifestCapsuleState.controller.abort();
  signedManifestCapsuleState.controller = null;
}

function updateSignedManifestCapsuleAvailability() {
  signedManifestCapsuleButton.disabled = (
    !signedManifestCapsuleState.token
    || !signedManifestCapsuleSessionId()
    || !signedManifestPairTerminalStates.has(signedManifestCapsuleSessionStatus())
  );
}

function signedManifestCapsuleCanonical(capsule) {
  return JSON.stringify({
    algorithm: capsule.algorithm,
    key_fingerprint: capsule.key_fingerprint,
    manifest_payload: capsule.manifest_payload,
    manifest_sha256: capsule.manifest_sha256,
    schema_version: capsule.schema_version,
    signature_payload: capsule.signature_payload,
  }) + "\n";
}

function signedManifestCapsuleBase64urlEncode(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function signedManifestCapsuleBase64urlDecode(text, label) {
  if (
    typeof text !== "string"
    || !signedManifestCapsuleBase64urlPattern.test(text)
    || text.length % 4 === 1
  ) {
    throw new Error(`${label} is not canonical base64url-without-padding text`);
  }
  const padded = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - text.length % 4) % 4);
  let binary;
  try {
    binary = atob(padded);
  } catch (_error) {
    throw new Error(`${label} is not valid base64url text`);
  }
  const decoded = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    decoded[index] = binary.charCodeAt(index);
  }
  if (signedManifestCapsuleBase64urlEncode(decoded) !== text) {
    throw new Error(`${label} is not canonical base64url-without-padding text`);
  }
  return decoded;
}

async function signedManifestCapsuleFetch(sessionId, token, generation, controller) {
  const response = await fetch(
    `/v1/sessions/${encodeURIComponent(sessionId)}/evidence/signed-manifest-capsule`,
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
  if (!signedManifestCapsuleIsCurrent(sessionId, generation) || controller.signal.aborted) return null;
  if (response.status === 401) {
    signedManifestCapsuleState.token = null;
    throw new Error("operator token was rejected; unlock the operator view again");
  }
  if (!response.ok) throw new Error(await signedManifestPairResponseError(response));
  if (!signedManifestCapsuleIsCurrent(sessionId, generation) || controller.signal.aborted) return null;

  if ((response.headers.get("Content-Type") || "").toLowerCase() !== "application/json; charset=utf-8") {
    throw new Error("signed manifest capsule returned an unexpected content type");
  }
  if (
    response.headers.get("Content-Disposition")
    !== 'attachment; filename="session-evidence-signed-manifest-pair.json"'
  ) {
    throw new Error("signed manifest capsule returned an unexpected filename");
  }
  const capsuleSha256 = response.headers.get("X-Harness-X-Evidence-Capsule-SHA256") || "";
  const manifestSha256 = response.headers.get("X-Harness-X-Evidence-Manifest-SHA256") || "";
  const keyFingerprint = response.headers.get("X-Harness-X-Evidence-Signature-Key") || "";
  const algorithm = response.headers.get("X-Harness-X-Evidence-Signature-Algorithm") || "";
  if (!signedManifestPairShaPattern.test(capsuleSha256)) {
    throw new Error("signed manifest capsule returned an invalid capsule SHA-256");
  }
  if (!signedManifestPairShaPattern.test(manifestSha256)) {
    throw new Error("signed manifest capsule returned an invalid manifest SHA-256");
  }
  if (!signedManifestPairKeyPattern.test(keyFingerprint)) {
    throw new Error("signed manifest capsule returned an invalid key fingerprint");
  }
  if (algorithm !== "ed25519") {
    throw new Error("signed manifest capsule returned an unexpected algorithm");
  }

  const declaredLength = signedManifestPairLength(response, "signed manifest capsule");
  const bytes = await response.arrayBuffer();
  if (!signedManifestCapsuleIsCurrent(sessionId, generation) || controller.signal.aborted) return null;
  if (bytes.byteLength !== declaredLength) {
    throw new Error("signed manifest capsule byte count does not match Content-Length");
  }
  const observedCapsuleSha256 = await signedManifestPairSha256Hex(bytes);
  if (!signedManifestCapsuleIsCurrent(sessionId, generation) || controller.signal.aborted) return null;
  if (observedCapsuleSha256 !== capsuleSha256) {
    throw new Error("signed manifest capsule bytes do not match the declared SHA-256");
  }

  const { value: capsule, text: capsuleText } = signedManifestPairJson(
    bytes,
    "signed manifest capsule",
  );
  if (!signedManifestPairHasExactKeys(capsule, signedManifestCapsuleKeys)) {
    throw new Error("signed manifest capsule returned unexpected fields");
  }
  if (capsule.schema_version !== "app-signed-manifest-capsule-v1") {
    throw new Error("signed manifest capsule returned an unexpected schema version");
  }
  if (capsule.algorithm !== algorithm) {
    throw new Error("signed manifest capsule algorithm does not match its response header");
  }
  if (capsule.key_fingerprint !== keyFingerprint) {
    throw new Error("signed manifest capsule key fingerprint does not match its response header");
  }
  if (capsule.manifest_sha256 !== manifestSha256) {
    throw new Error("signed manifest capsule manifest SHA does not match its response header");
  }
  if (capsuleText !== signedManifestCapsuleCanonical(capsule)) {
    throw new Error("signed manifest capsule body is not the canonical M55 serialization");
  }

  const manifestBytes = signedManifestCapsuleBase64urlDecode(
    capsule.manifest_payload,
    "signed manifest capsule manifest payload",
  );
  const signatureBytes = signedManifestCapsuleBase64urlDecode(
    capsule.signature_payload,
    "signed manifest capsule signature payload",
  );
  const observedManifestSha256 = await signedManifestPairSha256Hex(manifestBytes.buffer);
  if (!signedManifestCapsuleIsCurrent(sessionId, generation) || controller.signal.aborted) return null;
  if (observedManifestSha256 !== manifestSha256) {
    throw new Error("signed manifest capsule embedded manifest bytes do not match the declared SHA-256");
  }

  const { value: manifest } = signedManifestPairJson(
    manifestBytes.buffer,
    "signed manifest capsule embedded manifest",
  );
  if (manifest.schema_version !== "app-terminal-evidence-manifest-v1") {
    throw new Error("signed manifest capsule embedded manifest has an unexpected schema version");
  }
  if (manifest.session_id !== sessionId) {
    throw new Error("signed manifest capsule embedded manifest belongs to a different session");
  }
  if (!signedManifestPairShaPattern.test(manifest.fingerprint || "")) {
    throw new Error("signed manifest capsule embedded manifest has an invalid self-fingerprint");
  }
  if (
    !manifest.lifecycle
    || !signedManifestPairTerminalStates.has(manifest.lifecycle.status)
    || !signedManifestPairShaPattern.test(manifest.lifecycle.ledger_head_hash || "")
  ) {
    throw new Error("signed manifest capsule embedded manifest has invalid lifecycle evidence");
  }
  if (
    !manifest.coding_report
    || !signedManifestPairAvailabilityStates.has(manifest.coding_report.availability)
    || !manifest.causal_trace
    || !signedManifestPairAvailabilityStates.has(manifest.causal_trace.availability)
  ) {
    throw new Error("signed manifest capsule embedded manifest has invalid component availability");
  }

  const { value: envelope, text: envelopeText } = signedManifestPairJson(
    signatureBytes.buffer,
    "signed manifest capsule embedded signature",
  );
  if (!signedManifestPairHasExactKeys(envelope, signedManifestPairEnvelopeKeys)) {
    throw new Error("signed manifest capsule embedded signature has unexpected envelope fields");
  }
  if (envelope.schema_version !== "app-evidence-signature-v1") {
    throw new Error("signed manifest capsule embedded signature has an unexpected schema version");
  }
  if (envelope.algorithm !== algorithm) {
    throw new Error("signed manifest capsule embedded signature has an unexpected algorithm");
  }
  if (envelope.key_fingerprint !== keyFingerprint) {
    throw new Error("signed manifest capsule embedded signature key does not match capsule metadata");
  }
  if (envelope.manifest_sha256 !== manifestSha256) {
    throw new Error("signed manifest capsule embedded signature refers to different manifest bytes");
  }
  if (!signedManifestPairSignaturePattern.test(envelope.signature || "")) {
    throw new Error("signed manifest capsule contains non-canonical Ed25519 signature text");
  }
  if (envelopeText !== signedManifestPairCanonicalEnvelope(envelope)) {
    throw new Error("signed manifest capsule embedded signature is not the canonical M52 envelope serialization");
  }

  return { bytes, manifestSha256, keyFingerprint };
}

function signedManifestCapsuleClickDownload(bytes) {
  const blob = new Blob([bytes], { type: "application/json;charset=utf-8" });
  const objectUrl = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = "session-evidence-signed-manifest-pair.json";
    document.body.append(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

async function downloadSignedManifestCapsule() {
  const sessionId = signedManifestCapsuleSessionId();
  const token = signedManifestCapsuleState.token;
  if (
    !token
    || !sessionId
    || !signedManifestPairTerminalStates.has(signedManifestCapsuleSessionStatus())
  ) return;

  const generation = signedManifestCapsuleState.generation;
  cancelSignedManifestCapsuleDownload();
  const controller = new AbortController();
  signedManifestCapsuleState.controller = controller;
  signedManifestCapsuleButton.disabled = true;
  signedManifestCapsuleStatus.textContent = "Preparing server-atomic signed manifest capsule…";

  try {
    const capsule = await signedManifestCapsuleFetch(
      sessionId,
      token,
      generation,
      controller,
    );
    if (!capsule) return;
    if (!signedManifestCapsuleIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    signedManifestCapsuleClickDownload(capsule.bytes);
    signedManifestCapsuleStatus.textContent = (
      `Signed manifest capsule download initiated · sha256 ${capsule.manifestSha256}`
      + ` · key ${capsule.keyFingerprint}`
    );
  } catch (error) {
    if (!signedManifestCapsuleIsCurrent(sessionId, generation) || controller.signal.aborted) return;
    signedManifestCapsuleStatus.textContent = (
      `Signed manifest capsule download failed: ${signedManifestPairMessage(error)}`
    );
  } finally {
    if (signedManifestCapsuleState.controller === controller) signedManifestCapsuleState.controller = null;
    updateSignedManifestCapsuleAvailability();
  }
}

signedManifestCapsuleById("auth-form").addEventListener("submit", () => {
  const token = signedManifestCapsuleById("token").value.trim();
  if (token) signedManifestCapsuleState.token = token;
  updateSignedManifestCapsuleAvailability();
});

signedManifestCapsuleById("lock-button").addEventListener("click", () => {
  signedManifestCapsuleState.generation += 1;
  cancelSignedManifestCapsuleDownload();
  signedManifestCapsuleState.token = null;
  signedManifestCapsuleStatus.textContent = "";
  updateSignedManifestCapsuleAvailability();
});

new MutationObserver(() => {
  signedManifestCapsuleState.generation += 1;
  cancelSignedManifestCapsuleDownload();
  signedManifestCapsuleStatus.textContent = "";
  updateSignedManifestCapsuleAvailability();
}).observe(signedManifestCapsuleById("session-id"), {
  childList: true,
  characterData: true,
  subtree: true,
});

new MutationObserver(updateSignedManifestCapsuleAvailability).observe(
  signedManifestCapsuleById("session-status"),
  {
    childList: true,
    characterData: true,
    subtree: true,
  },
);

new MutationObserver(() => {
  if (signedManifestCapsuleById("auth-state").textContent.trim() === "Locked") {
    signedManifestCapsuleState.generation += 1;
    cancelSignedManifestCapsuleDownload();
    signedManifestCapsuleState.token = null;
    signedManifestCapsuleStatus.textContent = "";
  }
  updateSignedManifestCapsuleAvailability();
}).observe(signedManifestCapsuleById("auth-state"), {
  childList: true,
  characterData: true,
  subtree: true,
});

signedManifestCapsuleButton.addEventListener("click", () => {
  void downloadSignedManifestCapsule();
});

window.addEventListener("beforeunload", cancelSignedManifestCapsuleDownload);
updateSignedManifestCapsuleAvailability();
