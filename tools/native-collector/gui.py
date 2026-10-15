"""Desktop collection controls; Windows credentials use per-user DPAPI encryption."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import uuid
import webbrowser

ROOT=Path(__file__).resolve().parent
STATE=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'TakealotCollector'
SITE='https://urban-electronic.github.io/takealot-selector/auto-pricing'

def protect(data, decrypt=False):
    if os.name!='nt': raise RuntimeError('本机不是Windows，不保存服务密钥')
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    result=Blob()
    function=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    function.restype=wintypes.BOOL
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(result)):
        raise RuntimeError('Windows加密存储不可用，服务密钥未保存')
    try: return ctypes.string_at(result.data,result.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes=[ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(ctypes.cast(result.data,ctypes.c_void_p))

def resolve_python():
    """与 run_background.py 保持一致：优先自带 playwright 的解释器，否则回退
    TakealotCollector 捆绑运行时。避免用缺 playwright 的解释器启动子采集器。"""
    try:
        import importlib.util
        if importlib.util.find_spec('playwright'):
            return sys.executable
    except Exception:
        pass
    candidate = STATE / 'runtime-cpython-313' / 'Scripts' / 'python.exe'
    return str(candidate) if candidate.exists() else sys.executable

class App:
    def __init__(self, window):
        self.window=window;self.process=None;self.stop_path=None;self.events=queue.Queue()
        STATE.mkdir(parents=True,exist_ok=True)
        self.key=tk.StringVar();self.status=tk.StringVar(value='准备就绪。点「常态采集」本机即可自主采集目录中到期且未暂停的 SKU，云端自动退避让路。')
        self.state=tk.StringVar(value='待机')
        self.profile=tk.StringVar(value=(STATE/'profile-path.txt').read_text(encoding='utf-8').strip() if (STATE/'profile-path.txt').exists() else str(STATE/'browser_profile'))
        self.remember=tk.BooleanVar(value=os.name=='nt')
        saved=STATE/'service-key.dpapi'
        if saved.exists():
            try: self.key.set(protect(saved.read_bytes(),True).decode())
            except Exception: self.status.set('已保存的连接配置无法解密，请重新填写网站API Key。')
        window.title('Takealot 采集');window.geometry('740x500')
        frame=ttk.Frame(window,padding=18);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Takealot 本机采集',font=('',18)).pack(anchor='w')
        ttk.Label(frame,text='内置原PricePilot解析核心；仅采集行情，调价由系统单独控制。').pack(anchor='w',pady=(4,14))
        ttk.Label(frame,text='首次设置：网站「系统设置 → 数据源 → API Key」').pack(anchor='w')
        ttk.Entry(frame,textvariable=self.key,show='●').pack(fill='x',pady=5)
        ttk.Checkbutton(frame,text='在此Windows账户加密记住连接配置',variable=self.remember).pack(anchor='w')
        row=ttk.Frame(frame);row.pack(fill='x',pady=10)
        ttk.Entry(row,textvariable=self.profile).pack(side='left',fill='x',expand=True)
        ttk.Button(row,text='选择原浏览器档案',command=self.choose).pack(side='right',padx=(8,0))
        buttons=ttk.Frame(frame);buttons.pack(fill='x',pady=8)
        for text,command in [('登录 / 配送设置',lambda:self.start('login')),('采集一轮',lambda:self.start('once')),('常态采集',lambda:self.start('continuous')),('暂停采集',self.stop),('打开系统',lambda:webbrowser.open(SITE))]:
            ttk.Button(buttons,text=text,command=command).pack(side='left',padx=(0,8))
        indicator=ttk.Frame(frame);indicator.pack(anchor='w',pady=(10,0))
        self.dot=tk.Label(indicator,text='●',font=('',14),foreground='#888888');self.dot.pack(side='left')
        self.state_label=tk.Label(indicator,textvariable=self.state,font=('',11,'bold'),foreground='#888888')
        self.state_label.pack(side='left',padx=(6,0))
        self.status_label=ttk.Label(frame,textvariable=self.status,wraplength=690);self.status_label.pack(anchor='w',pady=(4,8))
        self.log=tk.Text(frame,height=10,wrap='word',state='disabled');self.log.pack(fill='both',expand=True)
        ttk.Label(frame,text='推荐「常态采集」：本机自主采集目录中到期且未暂停的 SKU、云端自动让路；「采集一轮」仅处理网页已排队的任务（无排队则不采）；「登录 / 配送设置」仅在会话失效或首次使用时使用。',wraplength=690).pack(anchor='w',pady=(8,0))
        window.protocol('WM_DELETE_WINDOW',self.close);window.after(200,self.poll)

    def choose(self):
        folder=filedialog.askdirectory(title='选择原浏览器档案')
        if folder:self.profile.set(folder)

    def start(self,mode):
        if self.process and self.process.poll() is None:
            messagebox.showinfo('正在运行','请先暂停当前采集或关闭登录浏览器。');return
        key=self.key.get().strip()
        if mode!='login' and not key:
            messagebox.showinfo('首次连接设置','请复制网站系统设置中的API Key到上方输入框；只需设置一次。');return
        profile=Path(self.profile.get()).expanduser()
        if not profile.is_absolute(): messagebox.showerror('档案路径','请选择浏览器档案的绝对路径。');return
        try:
            (STATE/'profile-path.txt').write_text(str(profile),encoding='utf-8')
            saved=STATE/'service-key.dpapi'
            if key and self.remember.get(): saved.write_bytes(protect(key.encode()))
            elif saved.exists(): saved.unlink()
        except Exception as exc: messagebox.showerror('连接配置',str(exc));return
        self.stop_path=STATE/(str(uuid.uuid4())+'.stop')
        command=[resolve_python(),'-u',str(ROOT/'collector.py'),'--profile',str(profile),'--stop-file',str(self.stop_path)]
        if mode=='login': command.append('--login')
        elif mode=='once': command.append('--once')
        # continuous 模式：不加 --once，采集器长驻、每约30s领租约并刷新心跳，云端 worker 自动退避让路
        environment=os.environ.copy();environment['PP_COLLECTOR_KEY']=key;environment['PYTHONIOENCODING']='utf-8'
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        self.process=subprocess.Popen(command,cwd=ROOT,env=environment,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',creationflags=flags)
        if mode=='login':
            self.status.set('登录浏览器正在打开；完成登录和配送设置后，在本窗口点“完成登录”。')
        elif mode=='continuous':
            self.status.set('常态采集已启动：本机自主采集目录中到期且未暂停的 SKU，云端自动让路；点「暂停采集」可随时停止。')
        else:
            self.status.set('正在连接并启动采集…')
        if mode=='login':
            self.finish=ttk.Button(self.window,text='完成登录并保存',command=self.stop);self.finish.pack(pady=5)
        process=self.process
        def read():
            for line in process.stdout:self.events.put(line.rstrip())
            process.wait();self.events.put('采集器已停止。')
        threading.Thread(target=read,daemon=True).start()

    def stop(self):
        if self.stop_path and self.process and self.process.poll() is None:
            self.stop_path.touch();self.status.set('正在停止并释放未完成任务…')
        if hasattr(self,'finish'):self.finish.destroy();del self.finish

    def poll(self):
        while not self.events.empty():
            line=self.events.get()
            self.log.configure(state='normal');self.log.insert('end',line+'\n');self.log.see('end');self.log.configure(state='disabled')
            if line.startswith('AUTH_EXPIRED:'):
                self.status.set('⚠️ 登录会话已失效：请点「登录 / 配送设置」重新登录，然后再次「常态采集」。')
                self.status_label.configure(foreground='#c00')
            else:
                self.status.set(line)
                self.status_label.configure(foreground='black')
        self.window.after(200,self.poll)

    def close(self):
        self.stop()
        if self.process and self.process.poll() is None:
            self.window.after(300,self.close);return
        if self.stop_path:self.stop_path.unlink(missing_ok=True)
        self.window.destroy()

if __name__=='__main__':
    window=tk.Tk();App(window);window.mainloop()
