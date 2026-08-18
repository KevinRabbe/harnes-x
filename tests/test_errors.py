import pytest

from harness_x.core.errors import ErrorCode, Result


def test_result_has_exactly_one_branch() -> None:
    assert Result[int].ok(3).value == 3
    failed = Result[int].fail(ErrorCode.INVALID_INPUT, "bad", field="x")
    assert failed.error is not None
    assert failed.error.details["field"] == "x"

    with pytest.raises(ValueError):
        Result[int]()
