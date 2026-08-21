"""Software-owned local application process lifecycle for M26."""

from __future__ import annotations

import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

from .contracts import ApplicationProcessState, ApplicationServerSpec, ensure_artifact_path

_DEFAULT_APP_EXECUTABLES = frozenset(
    {"python", "python3", "node", "npm", "pnpm", "yarn"}
)
_PACKAGE_MANAGER_SUBCOMMANDS = frozenset({"run", "test"})


def _normalized_executable(value: str) -> str:
    name = Path(value).name.casefold()
    return name[:-4] if name.endswith(".exe") else name


def _validate_argv(argv: tuple[str, ...], allowed: frozenset[str]) -> None:
    executable = _normalized_executable(argv[0])
    if executable not in allowed:
        raise PermissionError(f"application executable {executable!r} is not allowed")
    if executable in {"npm", "pnpm", "yarn"}:
        if len(argv) < 2 or argv[1].casefold() not in _PACKAGE_MANAGER_SUBCOMMANDS:
            raise PermissionError(
                f"application process permits only {sorted(_PACKAGE_MANAGER_SUBCOMMANDS)} "
                f"for {executable}"
            )


def _inside(root: Path, relative: str) -> Path:
    target = (root / (relative.strip() or ".")).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("application cwd escapes task workspace") from exc
    return target


def _sanitized_env() -> dict[str, str]:
    keep = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "COMSPEC",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "VIRTUAL_ENV",
        "PYTHONPATH",
        "PYTHONHOME",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
    return env


class ApplicationProcessManager:
    """Own exactly one declared local development/application process."""

    def __init__(
        self,
        workspace_root: str | Path,
        artifact_root: str | Path,
        spec: ApplicationServerSpec,
        *,
        allowed_executables: frozenset[str] = _DEFAULT_APP_EXECUTABLES,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        if not self.workspace_root.is_dir():
            raise ValueError("application workspace must exist")
        self.artifact_root = Path(artifact_root).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.spec = spec
        self.allowed_executables = frozenset(
            _normalized_executable(item) for item in allowed_executables
        )
        _validate_argv(spec.argv, self.allowed_executables)
        self._cwd = _inside(self.workspace_root, spec.cwd)
        if not self._cwd.is_dir():
            raise NotADirectoryError(spec.cwd)
        self.stdout_path = ensure_artifact_path(self.artifact_root, "application.stdout.log")
        self.stderr_path = ensure_artifact_path(self.artifact_root, "application.stderr.log")
        self._stdout_handle = None
        self._stderr_handle = None
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> ApplicationProcessState:
        if self._process is not None:
            raise RuntimeError("application process already started")
        self.stdout_path.parent.mkdir(parents=True, exist_ok=True)
        self._stdout_handle = self.stdout_path.open("wb")
        self._stderr_handle = self.stderr_path.open("wb")
        kwargs: dict[str, object] = {
            "cwd": self._cwd,
            "env": _sanitized_env(),
            "stdin": subprocess.DEVNULL,
            "stdout": self._stdout_handle,
            "stderr": self._stderr_handle,
            "shell": False,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            self._process = subprocess.Popen(list(self.spec.argv), **kwargs)
            self._wait_until_ready()
        except BaseException:
            self.stop()
            raise
        return self.state()

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + self.spec.startup_timeout_seconds
        health_url = urljoin(self.spec.base_url.rstrip("/") + "/", self.spec.health_path.lstrip("/"))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        last_error = "application did not respond"
        while time.monotonic() < deadline:
            process = self._process
            if process is None:
                raise RuntimeError("application process disappeared during startup")
            returncode = process.poll()
            if returncode is not None:
                raise RuntimeError(
                    f"application process exited during startup with return code {returncode}"
                )
            try:
                request = urllib.request.Request(
                    health_url,
                    method="GET",
                    headers={"User-Agent": "Harness-X-M26-Health/1"},
                )
                with opener.open(request, timeout=1.0) as response:
                    if 200 <= int(response.status) < 500:
                        return
            except (OSError, urllib.error.URLError, TimeoutError) as exc:
                last_error = str(exc)
            time.sleep(0.1)
        raise TimeoutError(
            f"application server did not become ready at {health_url}: {last_error}"
        )

    def state(self) -> ApplicationProcessState:
        process = self._process
        returncode = process.poll() if process is not None else None
        return ApplicationProcessState(
            running=process is not None and returncode is None,
            pid=(process.pid if process is not None else None),
            base_url=self.spec.base_url,
            stdout_path=str(self.stdout_path),
            stderr_path=str(self.stderr_path),
            returncode=returncode,
        )

    def stop(self) -> ApplicationProcessState:
        process = self._process
        if process is not None and process.poll() is None:
            self._terminate_process_group(process)
        state = self.state()
        self._close_logs()
        return state

    def _terminate_process_group(self, process: subprocess.Popen[bytes]) -> None:
        timeout = self.spec.shutdown_timeout_seconds
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=timeout,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            if process.poll() is None:
                process.terminate()
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                process.terminate()
        try:
            process.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            pass
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=max(1.0, timeout),
                )
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                process.kill()
        try:
            process.wait(timeout=max(1.0, timeout))
        except subprocess.TimeoutExpired:
            pass

    def _close_logs(self) -> None:
        for handle_name in ("_stdout_handle", "_stderr_handle"):
            handle = getattr(self, handle_name)
            if handle is not None:
                try:
                    handle.flush()
                    handle.close()
                finally:
                    setattr(self, handle_name, None)

    def close(self) -> None:
        self.stop()

    def __enter__(self) -> "ApplicationProcessManager":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
