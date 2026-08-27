# Milestone 77 — Windows Portable Runtime Distribution

## Status

Implementation milestone stacked exactly on frozen M76
`2c280644689061052d34dd3bf0f6e76d02577184`.

This document is the first M77 branch commit and defines the milestone boundary before implementation.

## Objective

Turn the already-qualified Windows desktop host into an actually portable local artifact: after extracting one
Windows distribution directory, a user should be able to launch `HarnessX.exe` without first installing Harness X
into a separate Python environment and without first installing a matching .NET runtime.

M77 closes only the runtime-distribution gap deliberately left open by M65–M76. It does not create an installer,
updater, code-signing trust root, bundled model runtime, or new application authority.

## Required behavior

M77 should:

1. build a Windows App Server executable from the exact Harness X source tree using a pinned/declared packaging
   toolchain and include the packaged App Server's required Python runtime and ordinary application dependencies;
2. preserve the existing `harness-x-app-server` CLI/desktop-handshake behavior rather than introducing a second
   App Server implementation or protocol;
3. include the exact packaged App Server UI assets required by the frozen M76 product surface;
4. publish the .NET desktop host as `win-x64` self-contained output so the distribution does not require a
   separately installed .NET 8 runtime;
5. assemble the desktop host and packaged App Server into one inspectable portable directory with
   `harness-x-app-server.exe` adjacent to `HarnessX.exe`, allowing the inherited M65 runtime locator to select the
   bundled runtime without a file-picker or PATH dependency;
6. emit a deterministic manifest over the assembled distribution files containing only bounded relative path,
   byte size, and SHA-256 identity plus the Harness X software version;
7. validate the assembled manifest against the exact artifact bytes before upload;
8. run an end-to-end smoke test from the assembled directory with `HARNESS_X_APP_SERVER_EXECUTABLE` unset and
   normal PATH-based Harness X discovery disabled, proving the desktop host finds and starts the adjacent bundled
   App Server, receives the existing one-time desktop bootstrap handshake, enforces the loopback origin policy,
   and shuts the child down through the inherited stdin-lifetime boundary;
9. keep the normal source/development installation and CLI behavior unchanged when the distribution build extra
   is not selected; and
10. upload one exact-head Windows portable artifact only after the full inherited Python test/config gates and
    packaged desktop smoke have passed.

## Distribution boundary

The first M77 distribution is deliberately a portable extracted directory rather than an installer.

The directory may contain:

- self-contained .NET desktop runtime files required by the published `HarnessX.exe`;
- one adjacent `harness-x-app-server.exe` plus its software-owned packaged Python runtime/dependency directory;
- the frozen M76 browser UI assets embedded as App Server package data;
- WebView2 loader/application files produced by the existing Microsoft.Web.WebView2 package;
- one deterministic distribution manifest; and
- ordinary third-party runtime license/notices already emitted or required by the selected build tools where
  applicable.

The distribution must not contain:

- user project/chat/session state;
- access tokens, bootstrap tickets, reload capabilities, passwords, API keys, signing private keys, or remembered
  executable paths;
- workspace paths, chat/task text, attachments, evidence, traces, reports, memory, model outputs, or approvals;
- model weights, training data, adapters, CUDA runtimes, local inference servers, browser binaries, or GPU stacks;
- build caches, test results, source-control metadata, temporary PyInstaller work trees, or packaging secrets.

## Runtime discovery and override semantics

The inherited M65 explicit `HARNESS_X_APP_SERVER_EXECUTABLE` override remains operator-authoritative. M77 does not
remove the existing remembered-runtime, nearby virtual-environment, PATH, or manual-selection compatibility paths.

For the portable artifact, however, normal operation should require none of those fallbacks because the bundled
`harness-x-app-server.exe` is adjacent to the desktop executable and is therefore discovered by the existing
runtime locator.

The bundled executable is an implementation detail of the portable artifact, not a browser capability. Browser
code never receives its host path and cannot choose a different executable.

## WebView2 boundary

M77 does not bundle or install the Microsoft Edge WebView2 Evergreen Runtime. The desktop host continues to use
Microsoft.Web.WebView2 under its existing local-origin navigation policy. Machines without a usable WebView2
runtime must fail visibly through the existing desktop startup/error surface rather than downloading or installing
software automatically.

Bundling a fixed WebView2 runtime, an installer prerequisite bootstrapper, or OS-level runtime installation is a
separate future objective.

## App Server packaging boundary

The packaged App Server must be generated from a tiny entry point that delegates directly to
`harness_x.app_server.cli.main`. Packaging is transport only; M77 must not fork the CLI parser, HTTP protocol,
authentication, product store, conversation execution, approval, settings, resource, reliability, observatory,
evidence, coding-runtime, verifier, reasoning, memory, or improvement implementations.

The packaged runtime may include optional libraries already required by currently qualified local App Server
features when doing so avoids silent capability loss. It must not silently add model weights or external network
services.

The packaged executable must still:

- bind only to the inherited configured/loopback App Server host boundary;
- generate the same persistent bearer/bootstrap material through existing server-owned storage;
- emit the same `app-server-desktop-start-v1` handshake in desktop-host mode;
- keep one-time bootstrap material in the URL fragment rather than HTTP query;
- keep long-lived credentials out of desktop stdout handshake and browser-owned persistence;
- terminate normally when its desktop stdin lifetime pipe closes; and
- use the same durable App Server state root supplied by the desktop host under `%LOCALAPPDATA%\Harness X`.

## Distribution manifest

M77 may add one non-authoritative build manifest such as `harness-x-distribution-manifest.json`.

It should be canonical bounded JSON containing:

- schema version;
- Harness X package version;
- distribution kind / target runtime;
- sorted entries for every shipped file except the manifest itself;
- normalized slash-separated relative path;
- exact byte size; and
- lowercase SHA-256.

The manifest is a packaging integrity inventory only. It is not an M43 evidence manifest, M52 signature envelope,
M58 verification receipt, trusted timestamp, release signature, provenance attestation, SBOM, or code-signing
claim. It does not become browser/runtime/evidence authority.

## Authority and safety invariants

The following remain authoritative and unchanged:

- M65 desktop child-process ownership and exact loopback-origin navigation policy;
- M66–M75 durable Product/Conversation/App Server execution, context, approval, settings, resource, and reliability
  authorities;
- M76 Improvement Observatory read-only projection boundary;
- coding/tool permission and side-effect checks;
- verifier outputs for verification truth;
- M43/M52/M58/M60+ evidence/signature/receipt boundaries for evidence trust claims;
- system-improvement promotion/rollback authorities from the existing improvement stack.

M77 must not:

- make the distribution manifest evidence or release-signing authority;
- hard-code or package bearer tokens, bootstrap capabilities, user state, API credentials, signing keys, or
  developer-machine absolute paths;
- make the desktop shell inspect or inject the persistent App Server bearer;
- weaken loopback/Host/authentication/CORS/navigation constraints because the server is bundled;
- change browser APIs, approval scope, verification semantics, memory truth, evidence trust, model routing, or
  self-improvement authority;
- execute an installer, updater, package manager, PowerShell bootstrapper, registry mutation, service install,
  scheduled task, Start Menu/desktop shortcut creation, file association, firewall rule, or privilege elevation;
- download Python, .NET, WebView2, model weights, packages, or executables at runtime;
- claim reproducible builds, publisher identity, tamper-resistant release authenticity, or trusted provenance
  without a later explicit signing/provenance milestone.

## Qualification plan

Before freeze, one exact M77 head must demonstrate:

- exact frozen M76 base and this document as commit 1;
- the packaging entry point delegates only to the inherited App Server CLI implementation;
- the App Server package includes and serves the final frozen M76 UI asset surface;
- normal `pip install -e ".[dev]"`, full pytest, `harness-x --help`, and default config validation remain green on
  Ubuntu and Windows;
- Windows distribution-build dependencies are isolated from the ordinary install and explicitly declared;
- self-contained `win-x64` desktop publish succeeds;
- packaged App Server `--help` succeeds from its assembled artifact location;
- the assembled portable directory contains adjacent `HarnessX.exe` and `harness-x-app-server.exe`;
- an assembled-artifact smoke with explicit App Server override removed and PATH-based Harness X discovery disabled
  succeeds using the adjacent packaged server;
- the smoke validates the inherited desktop startup handshake, loopback navigation rule, and clean child shutdown;
- distribution-manifest generation is deterministic for identical bytes and rejects path escape, missing files,
  duplicate relative paths, size/digest mismatch, and manifest self-inclusion;
- manifest verification against the finished assembled directory succeeds before artifact upload;
- focused tests prove the manifest carries no absolute path, credential, user-state, or evidence-authority field;
- the uploaded artifact contains no App Server state directory, access token, server-info, user/project/session data,
  build work tree, source-control metadata, or model/training payload;
- the Windows build remains non-elevated and performs no installer/update/registry/service mutation;
- exact artifact file count, size, SHA-256 and producing head/run are recorded during freeze; and
- final source/diff/review/authority audit records the exact qualified M77 head and synthetic merge in the draft PR
  body only.

## Non-goals

M77 does not implement MSI/MSIX/Inno/WiX installation, uninstall, Start Menu shortcuts, auto-update, release channels,
code signing, Authenticode, timestamping, trusted release provenance, SBOM attestation, bundled WebView2 Evergreen,
ARM64 packaging, macOS/Linux desktop distribution, model/runtime download, local model bundling, CUDA packaging,
remote/cloud deployment, background services, multi-user installation, or any new verification/evidence/memory/
approval/model-routing/self-improvement authority.

A future installer/release-signing milestone may build on this portable artifact only after M77 is independently
qualified and frozen.
