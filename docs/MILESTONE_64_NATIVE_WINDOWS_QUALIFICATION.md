# Milestone 64 — Native Windows Qualification

## Authority

M64 is a narrow portability qualification milestone stacked exactly on frozen M63 head `fd7c2b5d4db7775bd6c5b076fd629452ed3d5034`.

It exists because a real native Windows 11 / PowerShell / Python 3.12 run of the exact frozen M63 tree exposed platform-specific failures that the Ubuntu-only CI lane could not see: `48 failed, 682 passed, 11 skipped`, while the CLI help and default configuration validation still passed.

M64 is not an automatic continuation of the primary feature sequence and adds no new Harness X capability. Its sole objective is to make the already-frozen M63 executable surface behave and qualify consistently on native Windows without weakening the existing Linux guarantees.

## Observed failure classes

The Windows run exposed four concrete classes:

1. `ProjectMemoryStore` persists state and then calls `os.fsync()` through a read-only descriptor. This works on Linux but raises `OSError: [Errno 9] Bad file descriptor` on Windows and cascades into project-memory, procedure-reliability, procedure-revision, campaign, profile-run, and App Server failures.
2. Git-backed isolation and one packaged-JavaScript source assertion inherit host checkout CRLF behavior. The observed dirty-baseline SHA `25f8d0b2...` is the SHA-256 of `operator-dirty\r\n`, while the test fixture intentionally wrote `operator-dirty\n`. Host newline conversion must not silently change isolation evidence or source-code assertions.
3. Symlink-boundary tests assume the test process is allowed to create symlinks. Standard Windows shells can reject creation with WinError 1314 unless Developer Mode/elevation is available. Product symlink rejection remains required; only test setup may skip when the host itself cannot construct the adversarial fixture.
4. CI currently runs only `ubuntu-latest`, so none of these native-Windows defects are continuously qualified.

## Required implementation

M64 must remain minimal and evidence-driven:

- make durable project-memory state replacement use a descriptor mode that supports `fsync` on Windows while preserving flush-before-atomic-replace semantics;
- make Git isolation deterministic in the presence of Windows `core.autocrlf`/checkout conversion rather than accepting host-dependent baseline hashes;
- make text-source assertions insensitive to checkout CRLF when newline bytes are not part of the behavior being tested;
- preserve every product-side symlink/non-regular-file rejection while making symlink fixture creation skip only when the operating system denies creation;
- add a native Windows GitHub Actions qualification lane using Python 3.12, the same editable dev install, full `pytest`, `harness-x --help`, and default config validation;
- keep the existing Ubuntu lane intact.

## Non-goals

M64 does not:

- add new App Server, evidence, signing, receipt, memory, controller, training, browser, or coding features;
- relax symlink safety checks or evidence file-boundary rules;
- change evidence schemas, hashes, signatures, trust claims, receipt semantics, or CLI command vocabulary;
- change model/runtime authority boundaries;
- claim support for every Windows configuration beyond the exact GitHub Actions/native-Python qualification surface;
- merge any milestone PR or alter the frozen M63 branch.

## Qualification gates

M64 may freeze only on one exact head for which:

- the M63 merge base is exact;
- diff audit shows only Windows-portability/qualification changes;
- Ubuntu full `pytest`, `harness-x --help`, and config validation pass;
- Windows full `pytest`, `harness-x --help`, and config validation pass;
- review submissions and review threads are rechecked;
- the PR remains draft/open/unmerged.

A passing Windows lane is required; local assumptions are not sufficient evidence.
