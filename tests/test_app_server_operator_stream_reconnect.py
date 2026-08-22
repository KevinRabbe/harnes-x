from __future__ import annotations

import re
from importlib.resources import files


def _client_source() -> str:
    return files("harness_x.app_server").joinpath("ui", "app.js").read_text(encoding="utf-8")


def test_stream_terminal_refresh_rechecks_selection_generation_before_rendering_closed() -> None:
    source = _client_source()
    guarded_terminal = re.compile(
        r"const terminal = await selectedSessionIsTerminal\(sessionId, generation\);\s+"
        r"if \(!selectionIsCurrent\(sessionId, generation\)\) return;\s+"
        r"if \(terminal === true\)"
    )
    assert len(guarded_terminal.findall(source)) == 2


def test_stream_reconnects_are_timer_cancelled_and_resume_from_current_cursor() -> None:
    source = _client_source()
    assert "for (const timer of state.streamReconnectTimers) clearTimeout(timer);" in source
    assert "state.streamReconnectTimers.clear();" in source
    assert source.count("currentCursor = streamPolicy.advanceCursor") == 2
    assert source.count("currentCursor,\n      generation,\n      failures + 1") == 4
    assert "restart(sessionId, cursor, generation, consecutiveFailures);" in source
