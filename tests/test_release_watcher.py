"""The release half of the chain: tag resolution and the status the job waits for.

Two failures are covered here because both were silent in production runs:

* the production job authorised its status read with a placeholder instead of the
  workflow token, so GitHub answered 401 and the job died in five seconds;
* the watcher resolved an annotated tag to the tag object sha, which is not on
  main, so the deploy was refused while the job waited nine minutes.

The tests read the real files: a policy can only be trusted if the shipped bytes
are the ones being checked.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
HELPER = ROOT / "scripts" / "release_tags.py"
WATCHER = ROOT / "scripts" / "release_watcher.sh"


def _load_helper():
    spec = importlib.util.spec_from_file_location("release_tags", HELPER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_release_job_authorises_with_the_workflow_token():
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "Authorization: Bearer ${GH_TOKEN}" in text
    # A literal placeholder is what broke the run: it looks like a token to a
    # reader and like garbage to the API.
    assert "Authorization: Bearer ***" not in text
    assert "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in text or "GH_TOKEN: ${{ github.token }}" in text


def test_release_job_survives_an_error_body():
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    # `.[]` on a JSON object (a 401 body) aborts jq with exit 5 and kills the step
    # before the retry loop ever runs.
    assert 'if type == "array"' in text
    assert "api-error" in text


def _fake_api(module, refs, tag_objects, commits):
    def api(path, _tok):
        path = path.split("/repos/", 1)[1].split("/", 2)[2]
        path = "/" + path
        if path.startswith("/git/refs/tags"):
            return refs
        if path.startswith("/git/tags/"):
            return {"object": {"sha": tag_objects[path.rsplit("/", 1)[1]], "type": "commit"}}
        if path.startswith("/commits/"):
            sha = path.rsplit("/", 1)[1]
            return {"commit": {"committer": {"date": commits[sha]}}}
        raise AssertionError(path)

    module.api = api
    module.token = lambda: "unused"


def _run(module, monkeypatch, capsys, tmp_path, refs, tag_objects, commits, state_files):
    _fake_api(module, refs, tag_objects, commits)
    for name, values in state_files.items():
        (tmp_path / name).write_text("".join(v + "\n" for v in values), encoding="utf-8")
    monkeypatch.setattr(module, "STATE", str(tmp_path))
    capsys.readouterr()
    assert module.main() == 0
    return capsys.readouterr().out.strip()


def test_annotated_tag_resolves_to_its_commit(monkeypatch, capsys, tmp_path):
    module = _load_helper()
    out = _run(
        module,
        monkeypatch,
        capsys,
        tmp_path,
        refs=[{"ref": "refs/tags/SP3-Release", "object": {"type": "tag", "sha": "b" * 40}}],
        tag_objects={"b" * 40: "c" * 40},
        commits={"c" * 40: "2026-11-14T16:20:05Z"},
        state_files={"deployed-tags": [], "failed-tags": []},
    )
    assert out.startswith("SP3-Release " + "c" * 40)


def test_already_deployed_commit_is_still_reported(monkeypatch, capsys, tmp_path):
    module = _load_helper()
    out = _run(
        module,
        monkeypatch,
        capsys,
        tmp_path,
        refs=[{"ref": "refs/tags/SP3-Release", "object": {"type": "commit", "sha": "d" * 40}}],
        tag_objects={},
        commits={"d" * 40: "2026-11-14T16:00:00Z"},
        state_files={"deployed-tags": ["d" * 40], "failed-tags": []},
    )
    assert out.startswith("SP3-Release " + "d" * 40)


def test_failed_commit_is_not_retried(monkeypatch, capsys, tmp_path):
    module = _load_helper()
    out = _run(
        module,
        monkeypatch,
        capsys,
        tmp_path,
        refs=[{"ref": "refs/tags/SP3-Release", "object": {"type": "commit", "sha": "e" * 40}}],
        tag_objects={},
        commits={"e" * 40: "2026-11-14T16:00:00Z"},
        state_files={"deployed-tags": [], "failed-tags": ["e" * 40]},
    )
    assert out == ""


def test_unrelated_tags_are_ignored(monkeypatch, capsys, tmp_path):
    module = _load_helper()
    out = _run(
        module,
        monkeypatch,
        capsys,
        tmp_path,
        refs=[
            {"ref": "refs/tags/v1.0.0", "object": {"type": "commit", "sha": "f" * 40}},
            {"ref": "refs/tags/backup-bfdc979", "object": {"type": "commit", "sha": "f" * 40}},
        ],
        tag_objects={},
        commits={"f" * 40: "2026-11-14T16:00:00Z"},
        state_files={"deployed-tags": [], "failed-tags": []},
    )
    assert out == ""


def test_watcher_answers_an_already_released_tag():
    text = WATCHER.read_text(encoding="utf-8")
    assert "already released" in text
    # The answer must be a success status, otherwise a re-published tag leaves the
    # release job waiting and then red.
    assert "already deployed on the VPS" in text
