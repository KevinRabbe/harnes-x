# Milestone 65 — Windows WebView2 Desktop Host

## Authority

M65 is stacked exactly on frozen M64:

`9d03153cd5e90806211988c1033b7ceb8a006756`

M64 remains frozen. M65 may not rewrite its portability qualification record or product semantics.

## Objective

Make Harness X usable as a normal local Windows desktop application for the single operator:

1. launch one Windows executable;
2. locate the existing Harness X App Server runtime even when Explorer launch does not inherit an activated Python environment;
3. start that App Server as a managed child process;
4. obtain one short-lived single-use UI bootstrap URL over private redirected stdio;
5. display the existing authenticated operator UI inside a WebView2 window;
6. keep navigation confined to the exact loopback App Server origin;
7. shut the App Server down cleanly when the desktop host exits.

The desktop host is a thin shell around the frozen App Server/UI. It does not introduce a second session engine, second authentication system, or second browser UI.

## Chosen desktop stack

- Windows-only C# WinForms host targeting .NET 8.
- Microsoft Edge WebView2 control.
- Pin the stable `Microsoft.Web.WebView2` SDK package used by this milestone rather than a prerelease package.
- Reuse the existing Harness X HTML/CSS/JavaScript operator UI unchanged unless a narrowly required desktop compatibility defect is found.

## App Server desktop handshake

M65 may add one explicit private-host mode to `harness-x-app-server` for the desktop parent process.

Required properties:

- loopback-only server behavior remains unchanged;
- port `0` is used so the OS chooses the port;
- the persistent bearer token is never placed in a URL or emitted by the handshake;
- the only privileged URL handed to the desktop host is the existing short-lived single-use bootstrap URL;
- the bootstrap ticket remains in the URL fragment, not the HTTP query string;
- desktop handshake output is allowed only when stdout is redirected, so the bootstrap URL is not printed to an interactive terminal;
- stdin is redirected and acts as the parent-lifetime channel;
- stdin EOF requests `server.shutdown()`, allowing normal `server_close()` and service cleanup;
- if the desktop parent crashes, pipe closure should cause the same shutdown path;
- existing `--open-ui` browser behavior remains available and unchanged.

## Desktop runtime discovery

A double-clicked Windows application must not depend on an activated PowerShell or command-prompt environment.

The desktop shell therefore resolves the existing `harness-x-app-server.exe` in this order:

1. explicit `HARNESS_X_APP_SERVER_EXECUTABLE` operator override;
2. the previously remembered executable path under `%LOCALAPPDATA%\Harness X`;
3. an App Server executable adjacent to the desktop application;
4. `.venv\Scripts\harness-x-app-server.exe` or `venv\Scripts\harness-x-app-server.exe` while walking upward from the current directory and desktop executable directory;
5. the current process `PATH`;
6. if none is found, a one-time Windows file picker restricted to an existing `harness-x-app-server.exe`.

Automatically discovered or manually selected canonical App Server executables are remembered for future Explorer launches. A stale or missing remembered file is ignored and discovery continues. Remembering the path is best-effort and is not an authentication or trust assertion; the explicit environment override remains operator-authoritative.

## Desktop process ownership

The Windows host must:

- start the resolved App Server executable without shell interpolation;
- redirect stdin/stdout/stderr;
- parse exactly one structured startup handshake;
- fail visibly if the child exits before a valid handshake;
- create WebView2 only after a valid loopback bootstrap URL is obtained;
- use a persistent WebView2 user-data directory under the operator's local application-data directory;
- cancel or open externally any navigation that leaves the exact App Server loopback origin;
- close child stdin on normal window shutdown and wait for graceful server exit before falling back to process-tree termination;
- never read or inject the persistent bearer token itself.

The Windows pip console-script launcher may hand execution to a separate Python process. Therefore PID equality between the process started by the desktop shell and the server-reported PID is not an ownership invariant; the private redirected stdio channel is the desktop ownership boundary.

## Local paths

Default personal-use state should live under the current operator's local application-data directory rather than the repository checkout. The desktop shell derives its App Server root, WebView2 user-data root, and remembered App Server executable path from `%LOCALAPPDATA%\Harness X`.

## Distribution boundary

M65 is **not** the installer milestone.

It does not yet:

- bundle Python;
- bundle Harness X wheels/dependencies;
- install Start Menu/Desktop shortcuts;
- install or update the WebView2 Evergreen Runtime;
- provide automatic updates;
- provide MSI/MSIX/Inno/WiX packaging;
- sign Windows binaries;
- modify GPU/model dependency installation.

A later distribution objective may package the qualified M65 desktop executable together with a managed Harness X runtime.

## Qualification

Freeze requires one exact head with:

- full existing pytest on Ubuntu;
- full existing pytest on Windows;
- existing `harness-x --help` and default-config validation on both OS lanes;
- focused tests for desktop handshake security and stdin-EOF shutdown behavior;
- Windows `.NET 8` restore/build/publish of the desktop host;
- a non-interactive Windows desktop-host smoke test that validates runtime discovery/remembering, startup-handshake parsing, exact-origin navigation policy, and child-process lifetime logic without requiring an interactive GUI session;
- source/diff audit proving the desktop host remains a shell over the existing App Server/UI;
- final PR review and review-thread recheck.

## Freeze claim

A qualified M65 may claim that Harness X has a native Windows WebView2 desktop host that locates and launches the existing local App Server/UI stack from a normal Explorer double-click workflow, with one-time manual runtime selection when automatic discovery cannot find the installed App Server.

It may not claim that Harness X has a production installer, bundled runtime, code-signed release, auto-updater, or public distribution package.
