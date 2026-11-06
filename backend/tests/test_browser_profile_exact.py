"""Execute the production selector with synthetic proc files, never real processes."""
import ast
import io
from pathlib import Path
from types import SimpleNamespace


def selector(source):
    tree = ast.parse(source)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_pids_by_profile')
    paths = {
        '/proc/11/cmdline': b'chromium\0--user-data-dir=/fixture/profile\0',
        '/proc/12/cmdline': b'chromium\0--user-data-dir=/fixture/profile-other\0',
        '/proc/13/cmdline': b'chromium\0--flag=--user-data-dir=/fixture/profile\0',
        '/proc/14/cmdline': b'chromium\0--user-data-dir=/fixture/profile/child\0',
    }
    scope = {'os': SimpleNamespace(path=SimpleNamespace(isdir=lambda p: True, abspath=lambda p: p, realpath=lambda p: p)),
             '_proc_entries': lambda: ['11', '12', '13', '14'],
             'open': lambda path, mode: io.BytesIO(paths[path])}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<production-selector>', 'exec'), scope)
    return scope['_pids_by_profile']


def test_exact_profile_and_negative_probe():
    source = (Path(__file__).parents[1] / 'services/quote_browser.py').read_text()
    exact = selector(source)
    assert exact('/fixture/profile') == [11]
    assert exact('') == []
    old = source.replace("needle in cmdline.split(b'\\0')", 'needle in cmdline')
    assert old != source
    assert selector(old)('/fixture/profile') == [11, 12, 13, 14]


if __name__ == '__main__':
    test_exact_profile_and_negative_probe()
    print('PASS exact argv selection, empty profile, old-code negative probe')
