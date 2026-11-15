"""Adapter to the *installed* Hermes execution engine.

Verified surface (hermes 0.23.0, 2026-11-15):

    hermes kanban --board <slug> create <title> --body ... --idempotency-key ...
        --max-runtime ... --max-retries ... --workspace ... [--json]
    hermes kanban --board <slug> show <task_id> --json
    hermes kanban --board <slug> runs <task_id> --json
    hermes kanban --board <slug> list --json
    hermes kanban --board <slug> dispatch [--max N] [--failure-limit N] [--json]
    hermes kanban --board <slug> block <task_id> <reason>
    hermes kanban --board <slug> archive <task_id>

Camilia never shells out with a string: every call is a list of arguments, a fixed
executable, a bounded timeout, and mutually exclusive commands.  A task id is
validated before being interpolated, so a value coming from HTTP cannot turn into
an option.

When the Hermes binary is absent the adapter reports ``available: False`` and the
API refuses to pretend an execution was started (control-plane principle 12).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any

TASK_ID = re.compile(r"^(?!-)[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")
BOARD_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")

# Kanban status -> Camilia execution status
STATUS_MAP = {
    "todo": "queued",
    "triage": "queued",
    "scheduled": "scheduled",
    "ready": "queued",
    "running": "running",
    "review": "awaiting_review",
    "requested_changes": "awaiting_review",
    "blocked": "blocked",
    "done": "succeeded",
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "archived": "archived",
}


class HermesUnavailable(RuntimeError):
    """Hermes is not installed or not callable on this host."""


class HermesError(RuntimeError):
    def __init__(self, command: str, status: int, message: str) -> None:
        super().__init__(f"hermes {command} failed ({status}): {message}")
        self.command = command
        self.status = status
        self.message = message


def _iso(value: Any) -> str | None:
    """The kanban CLI stores epoch seconds; the API speaks ISO-8601 UTC."""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(microsecond=0).isoformat()
    return str(value)


def map_status(kanban_status: str | None) -> str:
    return STATUS_MAP.get((kanban_status or "").lower(), "unknown")


class KanbanAdapter:
    """Thin, allow-listed wrapper around the Hermes kanban CLI."""

    def __init__(self, binary: str | None = None, timeout: float = 45.0) -> None:
        self.binary = binary or os.environ.get("HERMES_BIN") or "hermes"
        self.timeout = float(os.environ.get("HERMES_KANBAN_TIMEOUT", timeout))

    # -- availability -------------------------------------------------------
    def resolved_binary(self) -> str | None:
        return shutil.which(self.binary)

    @property
    def available(self) -> bool:
        return self.resolved_binary() is not None

    def status(self) -> dict[str, Any]:
        path = self.resolved_binary()
        return {
            "available": bool(path),
            "binary": path,
            "engine": "hermes-kanban",
            "read_only": False,
        }

    # -- process plumbing ---------------------------------------------------
    def _run(self, args: list[str], *, expect_json: bool = True, stdin_text: str | None = None) -> Any:
        path = self.resolved_binary()
        if path is None:
            raise HermesUnavailable(f"hermes binary not found ({self.binary!r})")
        cmd = [path, *args]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                input=stdin_text,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HermesError(" ".join(args[:2]), 124, f"timeout after {self.timeout}s") from exc
        if proc.returncode != 0:
            raise HermesError(
                " ".join(args[:2]), proc.returncode, (proc.stderr or proc.stdout or "").strip()[:400]
            )
        out = (proc.stdout or "").strip()
        if not expect_json:
            return out
        if not out:
            return []
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise HermesError(" ".join(args[:2]), 0, f"invalid json: {out[:200]}") from exc

    @staticmethod
    def _check_board(board: str) -> str:
        if not BOARD_SLUG.match(board or ""):
            raise ValueError(f"invalid board slug: {board!r}")
        return board

    @staticmethod
    def _check_task(task_id: str) -> str:
        if not TASK_ID.match(task_id or ""):
            raise ValueError(f"invalid task id: {task_id!r}")
        return task_id

    # -- boards -------------------------------------------------------------
    def boards(self) -> list[dict[str, Any]]:
        return self._run(["kanban", "boards", "list", "--json"]) or []

    def ensure_board(self, slug: str, name: str | None = None) -> str:
        slug = self._check_board(slug)
        try:
            existing = {b.get("slug") for b in self.boards()}
        except HermesError:
            raise
        if slug not in existing:
            self._run(
                ["kanban", "boards", "create", slug, "--name", name or slug],
                expect_json=False,
            )
        return slug

    # -- tasks --------------------------------------------------------------
    def create_task(
        self,
        *,
        board: str,
        title: str,
        body: str,
        idempotency_key: str,
        max_runtime: str = "30m",
        max_retries: int = 1,
        workspace: str = "scratch",
        assignee: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        board = self._check_board(board)
        args = [
            "kanban",
            "--board",
            board,
            "create",
            title[:200],
            "--body-file",
            "-",
            "--idempotency-key",
            idempotency_key[:80],
            "--max-runtime",
            max_runtime,
            "--max-retries",
            str(max(0, min(int(max_retries), 5))),
            "--workspace",
            workspace if workspace in {"scratch", "worktree"} else "scratch",
            "--json",
        ]
        if assignee:
            args += ["--assignee", assignee]
        if model:
            args += ["--model", model]
        return self._run(args, stdin_text=body[:20000])

    def show(self, board: str, task_id: str) -> dict[str, Any]:
        """``show --json`` returns a document with ``task``/``runs``/``events``; flatten
        the task so callers do not have to know that nesting, and normalise the epoch
        timestamps the CLI emits into ISO-8601."""
        doc = self._run(["kanban", "--board", self._check_board(board), "show", self._check_task(task_id), "--json"])
        if not isinstance(doc, dict):
            raise HermesError("kanban show", 0, "unexpected payload")
        task = dict(doc.get("task") or {})
        task["run_count"] = len(doc.get("runs") or [])
        task["latest_summary"] = doc.get("latest_summary")
        task["events"] = len(doc.get("events") or [])
        task["started_at"] = _iso(task.get("started_at"))
        task["finished_at"] = _iso(task.get("completed_at") or task.get("ended_at"))
        return task

    def runs(self, board: str, task_id: str) -> list[dict[str, Any]]:
        return self._run(
            ["kanban", "--board", self._check_board(board), "runs", self._check_task(task_id), "--json"]
        ) or []

    def list_tasks(self, board: str, status: str | None = None) -> list[dict[str, Any]]:
        args = ["kanban", "--board", self._check_board(board), "list", "--json"]
        if status:
            args += ["--status", status]
        return self._run(args) or []

    def dispatch(self, board: str, max_spawns: int = 1, *, dry_run: bool = False) -> Any:
        args = [
            "kanban",
            "--board",
            self._check_board(board),
            "dispatch",
            "--max",
            str(max(1, min(int(max_spawns), 5))),
        ]
        if dry_run:
            args.append("--dry-run")
        args.append("--json")
        return self._run(args)

    def block(self, board: str, task_id: str, reason: str) -> str:
        return self._run(
            [
                "kanban",
                "--board",
                self._check_board(board),
                "block",
                self._check_task(task_id),
                reason[:400],
            ],
            expect_json=False,
        )

    def archive(self, board: str, task_id: str) -> str:
        return self._run(
            ["kanban", "--board", self._check_board(board), "archive", self._check_task(task_id)],
            expect_json=False,
        )
