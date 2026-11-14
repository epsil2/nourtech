# Security controls — sprint 0 scope

| Control | Where enforced | Evidence |
|---|---|---|
| Read-only host audit | `scripts/vps_audit.py` (argv lists, no shell, no mutating verbs) | `tests/test_vps_audit.py::test_collectors_never_issue_mutating_commands` |
| No published DB/cache port | `deploy/dev/docker-compose.yml` (no `ports:` anywhere, `internal: true`) | `tests/test_compose_policy.py`, CI `compose isolation check` |
| Resource ceilings | compose `deploy.resources.limits` | `test_every_service_has_healthcheck_and_resource_limits` |
| Least privilege in containers | non-root uid 10001, `no-new-privileges:true`, no docker socket | `test_no_host_network_and_no_privileged`, `test_no_docker_socket_mounted` |
| No secret in Git | `.env` git-ignored, secrets only through `${VAR:?}` | `test_secrets_are_not_inlined_in_compose`, CI `secret scan` |
| No credential in logs/telemetry | `redact()` / `redact_deep()` applied to the whole baseline document | `test_redact_removes_credentials`, `test_redact_deep_walks_containers` |
| Documented API surface only | `/healthz`, `/readyz`, FastAPI docs disabled | `test_docs_endpoints_are_disabled` |

## Known limitations

- The secret scan is grep-based; it catches credential-shaped strings, not
  arbitrary high-entropy secrets.
- Redaction covers known token shapes plus `password|secret|token|apikey`
  assignments. Free-form proprietary data is out of scope for sprint 0.
- No authentication on the API yet: `/readyz` discloses internal hostnames and
  port numbers, which is acceptable only because the network is internal and
  unreachable from the host. SCRUM-22 must add auth before any exposure.
