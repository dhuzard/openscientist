# FAIR-VCG deployment runbook

OpenScientist's FAIR-VCG overlay builds the upstream backend from reviewed commit
`11b0918c01062a0c9a388b33d28068982712d762`. The service has no host-published
port. OpenScientist and per-job agent containers reach it as
`http://fair-vcg-mentor:8000` on an attachable user-defined Docker bridge.

The deployment has two distinct gates:

1. The FAIR-VCG container liveness probe checks that `/openapi.json` responds.
2. The one-shot `fair-vcg-canary` validates API version `1.0.0`, the documented
   route methods, upload, metadata persistence, FAIR scoring, and the
   `prepare-v1`, `arrive-v2`, and `mnms-v1` templates using a synthetic CSV.

`openscientist` depends on successful canary completion. An unavailable service,
version mismatch, missing operation, malformed score, metadata mismatch, or
missing template therefore prevents application startup.

## Deploy

Docker Engine with Compose v2 and BuildKit must be available. The first build
requires outbound access to GitHub and Python package indexes. For multiple
stacks on one Docker host, set a unique bridge name in each stack's `.env`:

```dotenv
OPENSCIENTIST_AGENT_NETWORK=openscientist-prod-agent-runtime
```

Validate and start the combined model:

```console
docker compose -f docker-compose.yml -f docker-compose.fair-vcg.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.fair-vcg.yml up -d --build
```

Do not add a FAIR API key to the OpenScientist or agent environment.
`FAIR_PREPARE_URL` is the only FAIR setting forwarded to ordinary discovery and
report job containers. OpenScientist rejects locators containing embedded user
info, query strings, or fragments.

After a reviewed build, publish the image under an immutable registry digest for
production promotion and record the source commit, image digest, and
OpenScientist commit in the release manifest. The local
`fair-vcg-mentor:11b0918c0106` tag identifies the source revision but is not a
substitute for a registry retention policy.

## Verify

```console
docker compose -f docker-compose.yml -f docker-compose.fair-vcg.yml ps
docker compose -f docker-compose.yml -f docker-compose.fair-vcg.yml logs fair-vcg-canary
docker compose -f docker-compose.yml -f docker-compose.fair-vcg.yml run --rm fair-vcg-canary
docker network inspect "${OPENSCIENTIST_AGENT_NETWORK:-openscientist-agent-runtime}"
```

Expected canary output is one JSON object with `"compatible": true`, API version
`1.0.0`, and all three template IDs. The canary creates only synthetic rows.
FAIR-VCG should show port `8000/tcp` but no host binding.

For a spawned job, verify that Docker reports the configured runtime network and
the non-secret `FAIR_PREPARE_URL`. No `FAIR_*KEY`, token, or credential should
be present.

## Diagnose

- `fair-vcg-mentor` unhealthy: inspect its logs and volume permissions, then
  request `/openapi.json` from a container on the runtime bridge.
- `fair-vcg-canary` exits nonzero: its final stderr line names the contract
  failure. Do not bypass the completed-successfully dependency.
- Agent cannot resolve `fair-vcg-mentor`: confirm the value of
  `OPENSCIENTIST_AGENT_NETWORK`, inspect the bridge, and confirm the agent,
  OpenScientist, PostgreSQL, and FAIR-VCG containers are attached.
- API version or template mismatch: restore the pinned image. Upstream upgrades
  require updating the pin and compatibility constants together, rerunning the
  canary tests, and scientific/integration approval.

## Roll back

1. Capture `docker compose ... logs fair-vcg-mentor fair-vcg-canary`.
2. Stop the combined stack with the same two Compose files.
3. Restore the previous reviewed OpenScientist revision and FAIR-VCG image
   digest, then start the combined stack again.
4. Confirm the canary succeeds before accepting jobs.

Do not use `down -v` during a rollback. `fair_vcg_sessions` persists uploaded
assessment records, including synthetic canary records. Production operations
must define backup, retention, deletion, and volume-owner policy before live
study use. Deleting that named volume is an explicit destructive operation.

## Production prerequisites

- Security approval for the private bridge, Docker socket boundary, image
  provenance, and secret scan.
- A registry repository with immutable tags/digests and retention.
- A backup and retention policy for `fair_vcg_sessions`.
- Capacity limits and monitoring for FAIR-VCG latency, restart count, volume
  growth, canary failures, and application startup failures.
- Internal DNS/TLS configuration if the in-host bridge is replaced by a remote
  endpoint.
