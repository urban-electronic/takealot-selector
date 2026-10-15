"""Execute GUI callbacks with isolated process/widget fakes; no browser or credentials."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

spec = importlib.util.spec_from_file_location('collector_gui_modes', Path(__file__).resolve().parents[2] / 'tools/native-collector/gui.py')
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, 'STATE', tmp_path / 'state')
    gui.STATE.mkdir()
    instance = gui.App.__new__(gui.App)
    instance.process = None
    instance.stop_path = None
    instance.key = SimpleNamespace(get=lambda: 'isolated-test-key')
    instance.profile = SimpleNamespace(get=lambda: str(tmp_path / 'isolated-profile'))
    instance.remember = SimpleNamespace(get=lambda: False)
    instance.status = Mock()
    instance.window = Mock()
    monkeypatch.setattr(gui, 'messagebox', Mock())
    monkeypatch.setattr(gui.subprocess, 'Popen', Mock(return_value=SimpleNamespace(poll=lambda: None)))
    monkeypatch.setattr(gui.threading, 'Thread', Mock())
    monkeypatch.setattr(gui.ttk, 'Button', Mock())
    return instance


@pytest.mark.parametrize('mode,flag', [('login', '--login'), ('once', '--once'), ('continuous', None)])
def test_gui_modes_build_only_expected_process_arguments(app, mode, flag):
    app.start(mode)
    call = gui.subprocess.Popen.call_args
    command = call.args[0]
    assert '--profile' in command and '--stop-file' in command
    assert ('--login' in command) == (flag == '--login')
    assert ('--once' in command) == (flag == '--once')
    assert 'isolated-test-key' not in command
    assert call.kwargs['env']['PP_COLLECTOR_KEY'] == 'isolated-test-key'
    assert hasattr(app, 'finish') == (mode == 'login')
    if mode == 'continuous':
        assert '正在连接' in app.status.set.call_args.args[0]


def test_gui_does_not_launch_duplicate_process(app):
    app.process = SimpleNamespace(poll=lambda: None)
    app.start('continuous')
    gui.subprocess.Popen.assert_not_called()
    gui.messagebox.showinfo.assert_called_once()


@pytest.mark.parametrize('mode', ['once', 'continuous'])
def test_collection_requires_config_but_login_does_not(app, mode):
    app.key = SimpleNamespace(get=lambda: '')
    app.start(mode)
    gui.subprocess.Popen.assert_not_called()
    app.start('login')
    gui.subprocess.Popen.assert_called_once()


def test_invalid_mode_cannot_fall_through_to_continuous(app):
    app.start('typo')
    gui.subprocess.Popen.assert_not_called()
    gui.messagebox.showerror.assert_called_once()


def test_pause_signals_local_process_with_its_own_stop_file(app):
    app.start('continuous')
    stop_path = app.stop_path
    assert not stop_path.exists()
    app.stop()
    assert stop_path.exists()
    gui.subprocess.Popen.assert_called_once()
    assert '停止' in app.status.set.call_args.args[0]
