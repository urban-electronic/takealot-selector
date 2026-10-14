"""Standalone Windows desktop bootstrap; no WorkBuddy dependency or stored API key."""
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT=Path(__file__).resolve().parent

def main():
    if sys.version_info < (3,10): raise RuntimeError('需要Python 3.10或以上版本')
    state=Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'TakealotCollector'
    # A previously failed venv may belong to an older/different Python interpreter.
    runtime=state/('runtime-'+sys.implementation.cache_tag)
    python=runtime/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    requirements=(ROOT/'requirements.txt').read_text(encoding='utf-8')
    marker=runtime/'installed-requirements.txt'
    if not python.exists(): venv.EnvBuilder(with_pip=True).create(runtime)
    if not marker.exists() or marker.read_text(encoding='utf-8')!=requirements:
        subprocess.run([str(python),'-m','pip','install','--upgrade','pip>=25.2'],check=True)
        subprocess.run([str(python),'-m','pip','install','--only-binary=:all:','-r',str(ROOT/'requirements.txt')],check=True)
        subprocess.run([str(python),'-m','playwright','install','chromium'],check=True)
        marker.write_text(requirements,encoding='utf-8')
    # All profile and connection settings are handled in the desktop controls.
    command=[str(python),str(ROOT/'gui.py')]
    return subprocess.call(command,cwd=str(ROOT))

if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc:
        print(str(exc) if isinstance(exc,RuntimeError) else '启动准备失败，请保留窗口中的安装错误后重试。')
        sys.exit(1)
