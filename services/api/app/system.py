"""Real host and factory numbers for the monitoring page.

No estimation, no rounding-up: every field comes from ``/proc``, ``statvfs`` or
the store. Anything unreadable is reported as ``None`` rather than guessed.
"""
from __future__ import annotations

import os
import shutil
from typing import Any


def _read(path: str, default: str = "") -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return default


def host_snapshot(path: str = "/") -> dict[str, Any]:
    load = _read("/proc/loadavg").split()
    meminfo: dict[str, int] = {}
    for line in _read("/proc/meminfo").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            meminfo[parts[0].rstrip(":")] = int(parts[1])
    total_kb = meminfo.get("MemTotal")
    available_kb = meminfo.get("MemAvailable")
    usage = None
    if total_kb and available_kb is not None:
        usage = round(100 * (total_kb - available_kb) / total_kb)

    disk = shutil.disk_usage(path)
    uptime = _read("/proc/uptime").split()

    # Inside a container this is the cgroup limit, which is the number that matters.
    cgroup_limit = None
    for candidate in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        raw = _read(candidate).strip()
        if raw.isdigit():
            cgroup_limit = int(raw)
            break
    if cgroup_limit is not None and cgroup_limit > (1 << 60):
        cgroup_limit = None

    return {
        "cpu_count": os.cpu_count(),
        "load_1m": float(load[0]) if load else None,
        "load_5m": float(load[1]) if len(load) > 1 else None,
        "load_15m": float(load[2]) if len(load) > 2 else None,
        "memory": {
            "total_kb": total_kb,
            "available_kb": available_kb,
            "used_percent": usage,
            "cgroup_limit_bytes": cgroup_limit,
        },
        "disk": {
            "path": path,
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "used_percent": round(100 * disk.used / disk.total) if disk.total else None,
        },
        "uptime_seconds": int(float(uptime[0])) if uptime else None,
    }


def factory_snapshot(store) -> dict[str, Any]:
    agents = store.list_agents()
    specs = store.list_agent_specs(limit=500)
    return {
        "agents": len(agents),
        "specifications": len(specs),
        "specifications_approved": sum(1 for s in specs if s.get("status") == "approved"),
        "runs_by_status": store.counts(),
        "audit_events": store.audit_count(),
    }
