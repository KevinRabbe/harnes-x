"use strict";

const reloadAuthStorageKey = "harness-x.operator.reload-ticket.v1";
const reloadAuthTicketPattern = /^[A-Za-z0-9_-]{43}$/;
const reloadAuthRenewalIntervalMs = 120000;
const reloadAuthNetworkRetryMs = 30000;
const reloadAuthBootstrapPresentAtLoad = window.location.hash.startsWith("#bootstrap=");

const reloadAuthState = {
  token: null,
  renewalTimer: null,
  mintGeneration: 0,
  authGeneration: 0,
};

function reloadAuthById(id) {
  return document.getElementById(id);
}

function reloadAuthMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function storedReloadCapability() {
  let value = null;
  try {
    value = sessionStorage.getItem(reloadAuthStorageKey);
  } catch (error) {
    console.warn("reload capability storage unavailable", error);
    return null;
  }
  if (value == null) return null;
  if (!reloadAuthTicketPattern.test(value)) {
    try {
      sessionStorage.removeItem(reloadAuthStorageKey);
    } catch (_error) {
      // Storage is best-effort; manual authentication remains available.
    }
    return null;
  }
  return value;
}

function removeStoredReloadCapability() {
  try {
    sessionStorage.removeItem(reloadAuthStorageKey);
  } catch (error) {
    console.warn("failed to clear reload capability", error);
  }
}

function storeReloadCapability(ticket) {
  if (!reloadAuthTicketPattern.test(ticket)) {
    throw new Error("reload-ticket response contains an invalid capability");
  }
  sessionStorage.setItem(reloadAuthStorageKey, ticket);
}

function cancelReloadRenewal() {
  if (reloadAuthState.renewalTimer != null) {
    clearTimeout(reloadAuthState.renewalTimer);
    reloadAuthState.renewalTimer = null;
  }
}

function scheduleReloadRenewal(delayMs = reloadAuthRenewalIntervalMs) {
  cancelReloadRenewal();
  if (!reloadAuthState.token) return;
  reloadAuthState.renewalTimer = setTimeout(() => {
    reloadAuthState.renewalTimer = null;
    const token = reloadAuthState.token;
    if (token) void mintReloadCapability(token);
  }, delayMs);
}

async function mintReloadCapability(token) {
  if (!token) return;
  const generation = reloadAuthState.mintGeneration + 1;
  reloadAuthState.mintGeneration = generation;
  const previousTicket = storedReloadCapability();
  let response;
  try {
    response = await fetch("/v1/operator/reload-ticket", {
      method: "POST",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ previous_ticket: previousTicket }),
      cache: "no-store",
      credentials: "omit",
    });
  } catch (error) {
    if (generation === reloadAuthState.mintGeneration && reloadAuthState.token === token) {
      console.warn("reload capability renewal failed", error);
      scheduleReloadRenewal(reloadAuthNetworkRetryMs);
    }
    return;
  }

  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = null;
  }
  if (generation !== reloadAuthState.mintGeneration || reloadAuthState.token !== token) return;

  if (
    !response.ok
    || !payload
    || payload.schema_version !== "app-operator-reload-ticket-v1"
    || typeof payload.ticket !== "string"
    || !reloadAuthTicketPattern.test(payload.ticket)
  ) {
    removeStoredReloadCapability();
    if (response.status === 401) reloadAuthState.token = null;
    cancelReloadRenewal();
    console.warn("reload capability issuance rejected", response.status);
    return;
  }

  try {
    storeReloadCapability(payload.ticket);
  } catch (error) {
    removeStoredReloadCapability();
    cancelReloadRenewal();
    console.warn("failed to persist reload capability", error);
    return;
  } finally {
    payload.ticket = "";
  }
  scheduleReloadRenewal();
}

async function restoreOperatorAfterReload() {
  if (reloadAuthBootstrapPresentAtLoad) return;
  const ticket = storedReloadCapability();
  if (!ticket) return;
  removeStoredReloadCapability();

  const generation = reloadAuthState.authGeneration;
  let response;
  try {
    response = await fetch("/v1/operator/reload", {
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
    if (generation === reloadAuthState.authGeneration) {
      reloadAuthById("auth-error").textContent = `Reload unlock failed: ${reloadAuthMessage(error)}`;
    }
    return;
  }

  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = null;
  }
  if (generation !== reloadAuthState.authGeneration) {
    if (payload && typeof payload.access_token === "string") payload.access_token = "";
    return;
  }
  if (
    !response.ok
    || !payload
    || payload.schema_version !== "app-operator-reload-v1"
    || typeof payload.access_token !== "string"
    || !payload.access_token
  ) {
    const detail = payload && (payload.detail || payload.error);
    reloadAuthById("auth-error").textContent = `Reload unlock failed: ${detail || `HTTP ${response.status}`}`;
    return;
  }

  const tokenField = reloadAuthById("token");
  const authForm = reloadAuthById("auth-form");
  const accessToken = payload.access_token;
  payload.access_token = "";
  tokenField.value = accessToken;
  authForm.requestSubmit();
  tokenField.value = "";
}

reloadAuthById("auth-form").addEventListener("submit", () => {
  reloadAuthState.authGeneration += 1;
  const token = reloadAuthById("token").value.trim();
  reloadAuthState.token = token || null;
  cancelReloadRenewal();
  if (token) void mintReloadCapability(token);
});

reloadAuthById("lock-button").addEventListener("click", () => {
  reloadAuthState.authGeneration += 1;
  reloadAuthState.mintGeneration += 1;
  reloadAuthState.token = null;
  cancelReloadRenewal();
  removeStoredReloadCapability();
});

window.addEventListener("DOMContentLoaded", () => {
  void restoreOperatorAfterReload();
});
