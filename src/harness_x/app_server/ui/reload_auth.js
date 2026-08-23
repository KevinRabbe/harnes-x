"use strict";

const reloadAuthStorageKey = "harness-x.operator.reload-ticket.v1";
const reloadAuthFamilyStorageKey = "harness-x.operator.reload-family.v1";
const reloadAuthTicketPattern = /^[A-Za-z0-9_-]{43}$/;
const reloadAuthFamilyPattern = /^[A-Za-z0-9_-]{43}$/;
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

function storedReloadFamily() {
  let value = null;
  try {
    value = sessionStorage.getItem(reloadAuthFamilyStorageKey);
  } catch (error) {
    console.warn("reload family storage unavailable", error);
    return null;
  }
  if (value == null) return null;
  if (!reloadAuthFamilyPattern.test(value)) {
    try {
      sessionStorage.removeItem(reloadAuthFamilyStorageKey);
    } catch (_error) {
      // Storage is best-effort; manual authentication remains available.
    }
    return null;
  }
  return value;
}

function removeStoredReloadFamily() {
  try {
    sessionStorage.removeItem(reloadAuthFamilyStorageKey);
  } catch (error) {
    console.warn("failed to clear reload family", error);
  }
}

function generateReloadFamily() {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const value of bytes) binary += String.fromCharCode(value);
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function ensureReloadFamily() {
  const existing = storedReloadFamily();
  if (existing) return existing;
  let family;
  try {
    family = generateReloadFamily();
    if (!reloadAuthFamilyPattern.test(family)) {
      throw new Error("generated reload family is invalid");
    }
    sessionStorage.setItem(reloadAuthFamilyStorageKey, family);
  } catch (error) {
    removeStoredReloadFamily();
    console.warn("failed to establish reload family", error);
    return null;
  }
  return family;
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
    const family = token ? ensureReloadFamily() : null;
    if (token && family) void mintReloadCapability(token, family);
  }, delayMs);
}

async function mintReloadCapability(token, family) {
  if (!token || !reloadAuthFamilyPattern.test(family)) return;
  const generation = reloadAuthState.mintGeneration + 1;
  reloadAuthState.mintGeneration = generation;
  const previousTicket = storedReloadCapability();
  let response;
  try {
    response = await fetch("/v1/operator/reload-family-ticket", {
      method: "POST",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ previous_ticket: previousTicket, family }),
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
  if (generation !== reloadAuthState.mintGeneration || reloadAuthState.token !== token) {
    if (
      response.ok
      && payload
      && payload.schema_version === "app-operator-reload-family-ticket-v1"
      && typeof payload.ticket === "string"
      && reloadAuthTicketPattern.test(payload.ticket)
    ) {
      const staleTicket = payload.ticket;
      payload.ticket = "";
      void revokeReloadCapability(token, staleTicket);
    }
    return;
  }

  if (
    !response.ok
    || !payload
    || payload.schema_version !== "app-operator-reload-family-ticket-v1"
    || typeof payload.ticket !== "string"
    || !reloadAuthTicketPattern.test(payload.ticket)
  ) {
    const possibleTicket = (
      payload
      && typeof payload.ticket === "string"
      && reloadAuthTicketPattern.test(payload.ticket)
    ) ? payload.ticket : null;
    if (payload && typeof payload.ticket === "string") payload.ticket = "";
    removeStoredReloadCapability();
    cancelReloadRenewal();
    if (response.ok) {
      removeStoredReloadFamily();
      if (possibleTicket) void revokeReloadCapability(token, possibleTicket);
      void revokeReloadFamily(token, family);
    } else {
      if (response.status === 401) reloadAuthState.token = null;
      if (response.status === 409) removeStoredReloadFamily();
    }
    console.warn("reload capability issuance rejected", response.status);
    return;
  }

  try {
    storeReloadCapability(payload.ticket);
  } catch (error) {
    removeStoredReloadCapability();
    removeStoredReloadFamily();
    cancelReloadRenewal();
    void revokeReloadFamily(token, family);
    console.warn("failed to persist reload capability", error);
    return;
  } finally {
    payload.ticket = "";
  }
  scheduleReloadRenewal();
}

async function revokeReloadCapability(token, ticket) {
  if (!token || !reloadAuthTicketPattern.test(ticket)) return;
  try {
    const response = await fetch("/v1/operator/reload-revoke", {
      method: "POST",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ticket }),
      cache: "no-store",
      credentials: "omit",
    });
    if (!response.ok) {
      console.warn("reload capability revocation rejected", response.status);
    }
  } catch (error) {
    console.warn("reload capability revocation failed", error);
  }
}

async function revokeReloadFamily(token, family) {
  if (!token || !reloadAuthFamilyPattern.test(family)) return;
  try {
    const response = await fetch("/v1/operator/reload-family-revoke", {
      method: "POST",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ family }),
      cache: "no-store",
      credentials: "omit",
    });
    if (!response.ok) {
      console.warn("reload family revocation rejected", response.status);
    }
  } catch (error) {
    console.warn("reload family revocation failed", error);
  }
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
  const family = token ? ensureReloadFamily() : null;
  if (token && family) void mintReloadCapability(token, family);
});

reloadAuthById("lock-button").addEventListener("click", () => {
  const token = reloadAuthState.token;
  const ticket = storedReloadCapability();
  const family = storedReloadFamily();
  reloadAuthState.authGeneration += 1;
  reloadAuthState.mintGeneration += 1;
  reloadAuthState.token = null;
  cancelReloadRenewal();
  removeStoredReloadCapability();
  removeStoredReloadFamily();
  if (token && ticket) void revokeReloadCapability(token, ticket);
  if (token && family) void revokeReloadFamily(token, family);
});

window.addEventListener("DOMContentLoaded", () => {
  void restoreOperatorAfterReload();
});
