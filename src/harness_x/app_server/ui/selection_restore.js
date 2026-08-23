"use strict";

const operatorSelectionStorageKey = "harness-x.operator.selected-session.v1";
const operatorSelectionIdPattern = /^app_[0-9a-f]{32}$/;
const operatorSelectionBootstrapPresentAtLoad = window.location.hash.startsWith("#bootstrap=");

function storedOperatorSelection() {
  let value = null;
  try {
    value = sessionStorage.getItem(operatorSelectionStorageKey);
  } catch (error) {
    console.warn("operator selection storage unavailable", error);
    return null;
  }
  if (value == null) return null;
  if (!operatorSelectionIdPattern.test(value)) {
    clearStoredOperatorSelection();
    return null;
  }
  return value;
}

function clearStoredOperatorSelection() {
  try {
    sessionStorage.removeItem(operatorSelectionStorageKey);
  } catch (error) {
    console.warn("failed to clear operator selection", error);
  }
}

function storeOperatorSelection(sessionId) {
  if (!operatorSelectionIdPattern.test(sessionId)) return;
  try {
    sessionStorage.setItem(operatorSelectionStorageKey, sessionId);
  } catch (error) {
    console.warn("failed to persist operator selection", error);
  }
}

if (operatorSelectionBootstrapPresentAtLoad) clearStoredOperatorSelection();

const selectSessionBeforeReloadRestore = selectSession;
selectSession = async function selectSessionWithReloadRestore(sessionId) {
  if (typeof sessionId === "string" && operatorSelectionIdPattern.test(sessionId)) {
    storeOperatorSelection(sessionId);
  }
  return selectSessionBeforeReloadRestore(sessionId);
};

const unlockOperatorBeforeSelectionRestore = unlockOperator;
unlockOperator = async function unlockOperatorWithSelectionRestore(token) {
  await unlockOperatorBeforeSelectionRestore(token);
  if (operatorSelectionBootstrapPresentAtLoad) return;

  const sessionId = storedOperatorSelection();
  if (!sessionId) return;
  if (!state.sessions.has(sessionId)) {
    clearStoredOperatorSelection();
    return;
  }
  await selectSession(sessionId);
};

byId("lock-button").addEventListener("click", () => {
  clearStoredOperatorSelection();
});
