"use strict";

(async () => {
  const fragment = window.location.hash;
  if (!fragment.startsWith("#bootstrap=")) return;

  const match = /^#bootstrap=([A-Za-z0-9_-]{40,128})$/.exec(fragment);
  history.replaceState(null, "", "/ui/");
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
  const authForm = document.getElementById("auth-form");
  const accessToken = payload.access_token;
  payload.access_token = "";
  tokenField.value = accessToken;
  authForm.requestSubmit();
  tokenField.value = "";
})();
