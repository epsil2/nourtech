#!/usr/bin/env python3
"""Read-only VPS baseline audit for the AI Agent Factory (SCRUM-17).

The audit only *reads* host state: it never starts, stops, restarts or
reconfigures a service, container, network or volume. Every external read goes
through a single injectable callable so the collectors can be unit-tested
without docker or a live host.

Output: a JSON document and/or a Markdown report. Secrets are redacted before
they can reach either output.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Sequence, cast

# --- redaction -------------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(ATATT[A-Za-z0-9_\-=]{10,})"),                  # Atlassian token
    re.compile(r"(?i)\b(gh[pousr]_[A-Za-z0-9]{16,})"),                  # GitHub token
    re.compile(r"(?i)\b(github_pat_[A-Za-z0-9_]{20,})"),               # GitHub fine-grained PAT
    re.compile(r"(?i)\b(sk-[A-Za-z0-9_\-]{16,})"),                      # generic API key
    re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)(\s*[=:]\s*)(\S+)"),
]
REDACTED = "***REDACTED***"


def redact(value: str) -> str:
    """Remove credential-shaped substrings from free text."""
    out = value
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 3:
            out = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", out)
        else:
            out = pattern.sub(REDACTED, out)
    return out


def redact_deep(obj):
    """Recursively redact every string in a JSON-able structure."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_deep(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_deep(v) for v in obj]
    return obj


# --- command runner --------------------------------------------------------

Runner = Callable[[Sequence[str]], str]


def make_runner(timeout: int = 60) -> Runner:
    """Build the real read-only runner: argv list, no shell, timeout, no retries."""

    def run(argv: Sequence[str]) -> str:
        try:
            proc = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
            return f"<error: {type(exc).__name__}>"
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            return f"<exit {proc.returncode}: {detail[0] if detail else 'no output'}>"
        return proc.stdout.strip()

    return run


# --- collectors ------------------------------------------------------------

def _lines(raw: str) -> list[str]:
    return [line for line in raw.splitlines() if line.strip()]


def collect_host(run: Runner) -> dict:
    return {
        "hostname": platform.node(),
        "os": _lines(run(["sh", "-c", "grep PRETTY_NAME /etc/os-release | cut -d= -f2-"])),
        "kernel": run(["uname", "-r"]),
        "uptime": run(["uptime", "-p"]),
    }


def collect_resources(run: Runner) -> dict:
    cpu = run(["nproc"])
    mem_raw = _lines(run(["free", "-m"]))
    memory: dict[str, int] = {}
    if len(mem_raw) >= 2:
        cols = mem_raw[1].split()
        keys = ["total", "used", "free", "shared", "buff_cache", "available"]
        for name, val in zip(keys, cols[1:]):
            try:
                memory[name + "_mib"] = int(val)
            except ValueError:
                pass
    disk = _lines(run(["df", "-P", "/"]))
    return {
        "cpu_count": int(cpu) if cpu.isdigit() else None,
        "memory": memory,
        "root_disk": disk[-1].split() if len(disk) >= 2 else [],
    }


def collect_containers(run: Runner) -> list[dict]:
    raw = run(["docker", "ps", "--format", "{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}|{{.Label \"com.docker.compose.project\"}}"])
    out = []
    for line in _lines(raw):
        parts = line.split("|")
        if len(parts) < 5:
            continue
        out.append({
            "name": parts[0],
            "image": parts[1],
            "status": parts[2],
            "ports": parts[3],
            "compose_project": parts[4],
        })
    return out


def collect_inventory(run: Runner) -> dict:
    return {
        "compose_projects": sorted({c["compose_project"] for c in collect_containers(run) if c["compose_project"]}),
        "networks": [n.split("|")[0] for n in _lines(run(["docker", "network", "ls", "--format", "{{.Name}}|{{.Driver}}"]))],
        "volumes": _lines(run(["docker", "volume", "ls", "--format", "{{.Name}}"])),
    }


def collect_listeners(run: Runner) -> list[str]:
    raw = run(["ss", "-ltnH"])
    listeners = []
    for line in _lines(raw):
        cols = line.split()
        if len(cols) >= 4:
            listeners.append(cols[3])
    return sorted(set(listeners))


def collect_opt_projects(run: Runner) -> dict:
    raw = run(["sh", "-c", "du -sh /opt/* 2>/dev/null"])
    projects = {}
    for line in _lines(raw):
        parts = line.split("\t")
        if len(parts) == 2:
            projects[parts[1].strip()] = parts[0]
    return projects


def build_baseline(run: Runner | None = None, now: datetime | None = None) -> dict:
    """Assemble the full read-only baseline document."""
    run = run or make_runner()
    containers = collect_containers(run)
    baseline = {
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "read_only": True,
        "host": collect_host(run),
        "resources": collect_resources(run),
        "containers": containers,
        "inventory": collect_inventory(run),
        "listeners": collect_listeners(run),
        "opt_projects": collect_opt_projects(run),
    }
    baseline["summary"] = {
        "container_count": len(containers),
        "compose_projects": len(baseline["inventory"]["compose_projects"]),
        "listener_count": len(baseline["listeners"]),
    }
    return cast(dict, redact_deep(baseline))


# --- rendering -------------------------------------------------------------

def render_markdown(baseline: dict) -> str:
    host = baseline["host"]
    res = baseline["resources"]
    mem = res.get("memory", {})
    lines = [
        "# VPS baseline — AI Agent Factory",
        "",
        f"Generated: {baseline['generated_at']} · mode: **read-only**",
        "",
        "## Host",
        "",
        f"- hostname: `{host['hostname']}`",
        f"- os: {', '.join(host['os']) or 'n/a'}",
        f"- kernel: `{host['kernel']}`",
        f"- uptime: {host['uptime']}",
        "",
        "## Capacity",
        "",
        f"- vCPU: {res.get('cpu_count')}",
        f"- RAM: {mem.get('total_mib', 'n/a')} MiB total, {mem.get('available_mib', 'n/a')} MiB available",
        f"- root disk: {' '.join(res.get('root_disk', []))}",
        "",
        "## Containers",
        "",
        "| name | image | status | compose project |",
        "|---|---|---|---|",
    ]
    for c in baseline["containers"]:
        lines.append(f"| `{c['name']}` | `{c['image']}` | {c['status']} | {c['compose_project'] or '-'} |")
    lines += [
        "",
        "## Compose projects",
        "",
        ", ".join(f"`{p}`" for p in baseline["inventory"]["compose_projects"]) or "none",
        "",
        "## Listening sockets",
        "",
        ", ".join(f"`{p}`" for p in baseline["listeners"]) or "none",
        "",
        "## /opt projects",
        "",
    ]
    for path, size in sorted(baseline["opt_projects"].items()):
        lines.append(f"- `{path}` — {size}")
    lines.append("")
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only VPS baseline audit")
    parser.add_argument("--json", metavar="PATH", help="write JSON baseline to PATH ('-' for stdout)")
    parser.add_argument("--markdown", metavar="PATH", help="write Markdown report to PATH ('-' for stdout)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    baseline = build_baseline()
    payload = json.dumps(baseline, indent=2, sort_keys=True)
    markdown = render_markdown(baseline)

    if args.json:
        if args.json == "-":
            print(payload)
        else:
            with open(args.json, "w", encoding="utf-8") as handle:
                handle.write(payload + "\n")
            print(f"json baseline written to {args.json}", file=sys.stderr)
    if args.markdown:
        if args.markdown == "-":
            print(markdown)
        else:
            with open(args.markdown, "w", encoding="utf-8") as handle:
                handle.write(markdown + "\n")
            print(f"markdown report written to {args.markdown}", file=sys.stderr)
    if not args.json and not args.markdown:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
