"""运行时自检接口。只读，用来在故障当场看清线程/进程用量。

为什么需要它：2026-10-21 那次「所有图片 500」的根因是线程耗尽，但外部只能看到
`RuntimeError: can't start new thread`，没有任何接口能回答「现在多少线程、上限多少」。
有了这个接口，下次出现同类症状可以直接比对数字，而不是靠重启试探。
"""
from fastapi import APIRouter

from services import runtime_health

router = APIRouter(prefix="/api/diag", tags=["diag"])


@router.get("/runtime")
def runtime():
    """当前线程数、进程数、cgroup pids 用量与上限。"""
    return runtime_health.snapshot()
