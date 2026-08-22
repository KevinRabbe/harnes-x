"use strict";

(function installHarnessXStreamPolicy(globalObject) {
  const reconnectDelaysMs = Object.freeze([250, 500, 1000, 2000, 4000]);
  const terminalStatuses = new Set(["succeeded", "failed", "cancelled"]);

  function reconnectDelayMs(consecutiveFailureIndex) {
    if (!Number.isSafeInteger(consecutiveFailureIndex) || consecutiveFailureIndex < 0) {
      throw new Error("reconnect failure index must be a non-negative safe integer");
    }
    return reconnectDelaysMs[consecutiveFailureIndex] ?? null;
  }

  function advanceCursor(messageId, payloadCursor, currentCursor) {
    if (!Number.isSafeInteger(currentCursor) || currentCursor < 0) {
      throw new Error("current stream cursor must be a non-negative safe integer");
    }
    const parsedId = Number(messageId);
    if (!Number.isSafeInteger(parsedId) || parsedId < 1) {
      throw new Error("SSE event id must be a positive safe integer");
    }
    if (!Number.isSafeInteger(payloadCursor) || payloadCursor !== parsedId) {
      throw new Error("SSE id does not match the authoritative payload cursor");
    }
    if (parsedId !== currentCursor + 1) {
      throw new Error(
        `non-contiguous stream cursor: expected ${currentCursor + 1}, received ${parsedId}`,
      );
    }
    return parsedId;
  }

  function isTerminalStatus(status) {
    return terminalStatuses.has(String(status || ""));
  }

  globalObject.HarnessXStreamPolicy = Object.freeze({
    advanceCursor,
    isTerminalStatus,
    maxReconnectAttempts: reconnectDelaysMs.length,
    reconnectDelayMs,
  });
})(globalThis);
