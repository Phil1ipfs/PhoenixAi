"""Tiny RAM probe (Windows via ctypes, otherwise /proc/meminfo) to avoid a psutil dependency."""
from __future__ import annotations

import sys


def memory() -> dict:
    try:
        if sys.platform == "win32":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ae", ctypes.c_ulonglong)]

            s = MS()
            s.l = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
            return {"total_mb": s.total // 2**20, "available_mb": s.avail // 2**20}
        info = {}
        for line in open("/proc/meminfo"):
            k, v = line.split(":")
            info[k] = int(v.split()[0]) // 1024
        return {"total_mb": info["MemTotal"], "available_mb": info["MemAvailable"]}
    except Exception:  # noqa: BLE001
        return {"total_mb": None, "available_mb": None}


def assess_memory(available_mb: int | None, loaded: bool, model_mb: float, min_free_mb: int, num_ctx: int) -> dict:
    """Heuristic pre-flight check. Returns {action: ok|warn|block, message, num_ctx}.

    Calibrated on a 12 GB machine: a ~2 GB model loaded fine with ~1.6 GB "available" (Windows counts reclaimable
    cache), so blocking is deliberately conservative; warnings and a smaller context are the main tools.
    """
    if available_mb is None:
        return {"action": "ok", "num_ctx": num_ctx}
    need = 0.0 if loaded else model_mb * 1.1 + num_ctx * 0.12
    block_below = 150 if loaded else need * 0.4
    if available_mb < block_below:
        return {"action": "block", "num_ctx": num_ctx,
                "message": f"Only {available_mb} MB of RAM is free; loading the model needs roughly {round(need)} MB. "
                           "Close other applications (browser tabs, editors) and try again."}
    if available_mb < need + min_free_mb:
        return {"action": "warn", "num_ctx": min(num_ctx, 2048),
                "message": f"Low memory ({available_mb} MB free). Using a smaller context to reduce the risk of slowdowns."}
    return {"action": "ok", "num_ctx": num_ctx}
