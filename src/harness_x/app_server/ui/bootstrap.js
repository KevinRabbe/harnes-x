"use strict";

function loadConversationExecutionBridge() {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "/ui/execution_bridge.js";
    script.async = true;
    script.addEventListener("load", resolve, { once: true });
    script.addEventListener("error", () => reject(new Error("execution bridge asset failed to load")), { once: true });
    document.head.append(script);
  });
}

(async () => {
  const fragment = window.location.hash;
  const bootstrapPresent = fragment.startsWith("#bootstrap=");
  const match = bootstrapPresent
    ? /^#bootstrap=([A-Za-z0-9_-]{40,128})$/.exec(fragment)
    : null;
  if (bootstrapPresent) history.replaceState(null, "", "/ui/");

  const authForm = document.getElementById("auth-form");
  const authSubmit = authForm.querySelector('button[type="submit"]');
  authSubmit.disabled = true;
  try {
    await loadConversationExecutionBridge();
  } catch (error) {
    document.getElementById("auth-error").textContent = `Workspace initialization failed: ${error instanceof Error ? error.message : String(error)}`;
    authSubmit.disabled = false;
    return;
  }
  authSubmit.disabled = false;

  if (!bootstrapPresent) return;
  if (!match) {
    document.getElementById("auth-error").textContent = "Automatic unlock failed: invalid bootstrap fragment.";
    return;
  }

  const ticket = match[1];
  let response;
  try {
    response = await fetch("/v1/operator/bootstrap", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ticket }),
      cache: "no-store",
      credentials: "omit",
    });
  } catch (error) {
    document.getElementById("auth-error").textContent = `Automatic unlock failed: ${error instanceof Error ? error.message : String(error)}`;
    return;
  }

  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = null;
  }
  if (
    !response.ok
    || !payload
    || payload.schema_version !== "app-operator-bootstrap-v1"
    || typeof payload.access_token !== "string"
    || !payload.access_token
  ) {
    const detail = payload && (payload.detail || payload.error);
    document.getElementById("auth-error").textContent = `Automatic unlock failed: ${detail || `HTTP ${response.status}`}`;
    return;
  }

  const tokenField = document.getElementById("token");
  const accessToken = payload.access_token;
  payload.access_token = "";
  tokenField.value = accessToken;
  authForm.requestSubmit();
  tokenField.value = "";
})();
