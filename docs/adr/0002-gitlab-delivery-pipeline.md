# ADR 0002 — Delivery pipeline: GitHub for review, GitLab for release

- Status: accepted (SCRUM-21)
- Date: 2026-11-14

## Context

The sprint asks for a delivery path that separates *quality* from *production* and
keeps production behind a human gate. The code lives on GitHub with a pull-request
flow and a `ci.yml` workflow that runs on every pull request. The team also wants
the GitLab pipeline (`qualite` → `production`) as the release mechanism.

Two constraints shape the design: one vCPU / 3.8 GiB VPS that already runs ten
production containers, and a rule that no credential may live in the repository.

## Decision

1. **GitHub stays the source of truth.** Review, pull requests and fast feedback
   run there. Nothing is authored on GitLab.
2. **A one-way mirror pushes `main` and tags to GitLab** from a GitHub Actions job
   (`mirror-to-gitlab.yml`, `git push --prune --force`). GitLab never pushes back,
   so the mirror cannot diverge.
3. **The GitLab pipeline is the release gate**: `qualite` (tests, compose
   validation of both stacks, secret scan) then `production`, which is `manual`,
   default-branch only, serialised by `resource_group: production`, and `needs`
   every quality job. `allow_failure: false`, so a skipped production job keeps the
   pipeline red instead of silently green.
4. **Deployment runs over SSH to the VPS**, not on a GitLab runner hosted there:
   the host has no capacity to spare for a runner, and a runner would need the
   Docker socket — a privilege the deployment does not need.
5. **The API is reachable on loopback only** (`127.0.0.1:8081`). With only health
   endpoints today, public exposure would add attack surface for nothing.
6. **The image is tagged with the commit sha**, which is what makes the automatic
   rollback (`state/last-good`) possible without a registry.

## Consequences

- Two pipelines exist, but they do different jobs: GitHub checks a change, GitLab
  releases a commit. The mirror job is the only coupling between them.
- A GitLab outage blocks the *release*, never the *review*: a deploy can still be
  run by an operator with the same script.
- `DEPLOY_SSH_KEY`, `DEPLOY_HOST` and `DEPLOY_USER` are the only GitLab secrets;
  the database password never leaves the VPS.
- The mirror force-pushes, so a GitLab-side hotfix would be overwritten. That is
  intended: hotfixes go through GitHub.
