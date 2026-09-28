import json
from types import SimpleNamespace

import pytest

from work_hunter.browser_bridge import connect_browser_bridge


def test_bridge_does_not_fall_back_or_touch_hh(tmp_path):
    path = tmp_path / '.work-hunter' / 'browser-bridge.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'enabled': True, 'connected': False}))
    assert connect_browser_bridge(None, tmp_path, 'hh') is None
    with pytest.raises(RuntimeError, match='отключён'):
        connect_browser_bridge(None, tmp_path, 'habr')


def test_bridge_only_connects_to_local_pipe(tmp_path):
    path = tmp_path / '.work-hunter' / 'browser-bridge.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'enabled': True, 'endpoint': 'ws://remote.example/browser'}))
    with pytest.raises(RuntimeError, match='отключён'):
        connect_browser_bridge(None, tmp_path, 'habr')
    endpoint = r'\\.\pipe\pw-test-browser'
    path.write_text(json.dumps({'enabled': True, 'endpoint': endpoint}))
    browser = SimpleNamespace(contexts=[object()])
    calls = []
    playwright = SimpleNamespace(chromium=SimpleNamespace(connect=lambda value, **kw: calls.append(value) or browser))
    assert connect_browser_bridge(playwright, tmp_path, 'habr') is browser
    assert calls == [endpoint]
