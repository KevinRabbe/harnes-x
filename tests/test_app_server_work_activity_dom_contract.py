from harness_x.app_server.ui_assets import load_ui_asset


def _asset(path: str) -> str:
    asset = load_ui_asset(path)
    assert asset is not None
    _content_type, payload = asset
    return payload.decode("utf-8")


def test_m70_activity_region_uses_the_existing_m68_composer_class_contract() -> None:
    html = _asset("/ui/")
    javascript = _asset("/ui/execution_bridge.js")

    assert 'class="daily-composer-wrap"' in html
    assert 'document.querySelector(".daily-composer-wrap")' in javascript
    assert 'dailyById("daily-composer-wrap")' not in javascript
    assert 'throw new Error("daily composer wrapper is unavailable")' in javascript
