"""Exercise Api -> run -> persisted status/log; no browser/network/credentials."""
import asyncio
import json
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
import collector
from test_collector_flow import _install_fake_playwright, FakeParser, ok


@pytest.mark.parametrize('failure,expected', [
    (403, 'HTTP 403：服务拒绝访问'),
    (404, 'HTTP 404：采集接口不存在'),
    (302, '重定向：已拒绝转发密钥'),
    (429, 'HTTP 429：请求过于频繁'),
    (503, '服务暂不可用'),
    ('html', '服务响应格式异常'),
    ('array', '服务响应格式异常'),
])
def test_api_failure_survives_run_to_status_and_log(monkeypatch, tmp_path, capsys, failure, expected):
    _install_fake_playwright(monkeypatch)
    monkeypatch.setenv('PP_COLLECTOR_KEY', 'TEST_ONLY_SENTINEL')
    monkeypatch.setattr(collector, 'STATUS_FILE', tmp_path/'status.json')
    monkeypatch.setattr(collector, 'challenge_state_path', lambda profile: tmp_path/'challenge.json')
    monkeypatch.setattr(collector, 'parser_module', lambda: FakeParser(ok))
    monkeypatch.setattr(collector.time, 'sleep', lambda _: None)
    requests = []
    class Response(BytesIO):
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
    class Opener:
        def open(self, req, **kwargs):
            requests.append(req.full_url)
            if req.full_url.endswith('/status'):
                return Response(b'{"ready":true}')
            if isinstance(failure, int):
                raise HTTPError(req.full_url, failure, 'SERVER_SENTINEL', {},
                                BytesIO(b'{"detail":"SERVER_SENTINEL"}'))
            return Response(b'<html>SERVER_SENTINEL</html>' if failure == 'html' else b'[]')
    monkeypatch.setattr(collector.request, 'build_opener', lambda *args: Opener())
    args = SimpleNamespace(profile=str(tmp_path/'profile'), login=False, once=True, stop_file=None)
    asyncio.run(collector.run(args))
    status = json.loads((tmp_path/'status.json').read_text())
    output = capsys.readouterr().out
    assert status['state'] == 'failed'
    assert expected in status['error'] and status['error'] in output
    assert '服务连接被拒绝或超时' not in output
    assert 'SERVER_SENTINEL' not in output + json.dumps(status)
    assert 'TEST_ONLY_SENTINEL' not in output + json.dumps(status)
    assert all(url.endswith(('/status', '/products')) for url in requests)


def test_unclassified_exception_body_never_reaches_status_or_log(monkeypatch, tmp_path, capsys):
    _install_fake_playwright(monkeypatch)
    monkeypatch.setenv('PP_COLLECTOR_KEY', 'TEST_ONLY_SENTINEL')
    monkeypatch.setattr(collector, 'STATUS_FILE', tmp_path/'status.json')
    monkeypatch.setattr(collector, 'challenge_state_path', lambda profile: tmp_path/'challenge.json')
    monkeypatch.setattr(collector, 'parser_module', lambda: FakeParser(ok))
    class Api:
        def __init__(self, *args): pass
        def call(self, path, body=None):
            if path.endswith('/status'): return {'ready': True}
            raise RuntimeError('SERVER_SENTINEL')
    monkeypatch.setattr(collector, 'Api', Api)
    asyncio.run(collector.run(SimpleNamespace(profile=str(tmp_path/'profile'), login=False, once=True)))
    status = json.loads((tmp_path/'status.json').read_text())
    assert status['state'] == 'failed' and status['error'] == '页面或服务暂不可用'
    assert 'SERVER_SENTINEL' not in capsys.readouterr().out + json.dumps(status)
