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
    # Only the profile path is retained. Authentication is prompted by collector.py each run.
    profile_file=state/'profile-path.txt'
    profile=profile_file.read_text(encoding='utf-8').strip() if profile_file.exists() else ''
    if not profile or '--choose-profile' in sys.argv:
        import tkinter as tk
        from tkinter import filedialog,messagebox
        window=tk.Tk();window.withdraw()
        messagebox.showinfo('选择采集浏览器档案','已有原PricePilot会话：选择原browser_profile文件夹。\n没有原档案：取消选择，将创建独立档案；先双击login.cmd完成登录和配送设置。')
        chosen=filedialog.askdirectory(title='选择原PricePilot的browser_profile文件夹')
        window.destroy()
        profile=chosen or str(state/'browser_profile')
        profile_file.write_text(profile,encoding='utf-8')
    command=[str(python),str(ROOT/'collector.py'),'--profile',profile]
    if '--login' in sys.argv: command.append('--login')
    return subprocess.call(command,cwd=str(ROOT))

if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc:
        print(str(exc) if isinstance(exc,RuntimeError) else '启动准备失败，请保留窗口中的安装错误后重试。')
        sys.exit(1)
