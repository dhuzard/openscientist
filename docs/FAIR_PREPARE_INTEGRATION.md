# FAIR / PREPARE integration

OpenScientist delegates FAIR and reporting-guideline assessment to
[`Neuronautix/FAIR-VCG-mentor`](https://github.com/Neuronautix/FAIR-VCG-mentor).
The initial integration is pinned to commit
`11b0918c01062a0c9a388b33d28068982712d762` and uses its documented FastAPI
contract rather than importing web-application internals.

## Service contract

OpenScientist calls:

- `POST /api/upload`
- `PUT /api/metadata/{dataset_id}`
- `GET /api/fair-score/{dataset_id}`
- `POST /api/{dataset_id}/template/apply-from-paper`

The templates used by the DVC POC are:

- pre-analysis: `prepare-v1`, `arrive-v2`
- post-analysis: `arrive-v2`, `mnms-v1`

Set `FAIR_PREPARE_URL` to the reachable backend URL. The default is
`http://fair-vcg-mentor:8000`, intended for a shared Docker network where the
FAIR-VCG backend service is named `fair-vcg-mentor`.

The external service must be deployed separately from its pinned repository
revision. OpenScientist does not vendor or fork the FAIR-VCG rules.

## Checkpoints

### Pre-analysis

`dvc_assess_pre_analysis` serializes the versioned `PreclinicalStudyContext` to
a deterministic one-row CSV and submits it to FAIR-VCG. The resulting FAIR,
PREPARE and ARRIVE findings are persisted under:

```text
dvc_assessments/dvc-assess-<uuid>.json
```

Scientific approvals can only be created through the authenticated REST API and
must reference a matching pre-analysis checkpoint.

### Trusted approval API

```text
POST /api/v1/dvc/jobs/{job_id}/approvals
Authorization: Bearer <api-key-name>:<api-key-secret>
```

Request fields:

- `dataset_id`
- `operation`
- `context`
- `pre_analysis_checkpoint_id`

The API verifies job ownership, operation governance, dataset/checkpoint binding,
and writes two records:

- `<approval_id>.json`: strict executable approval consumed by the MCP tool
- `<approval_id>.audit.json`: job, dataset and assessment provenance

The agent cannot create or modify either record through MCP.

### Post-analysis

`dvc_assess_post_analysis` assembles a job-local bundle containing the acquisition
manifest, normalized measurements/events and an index of analysis provenance
records. FAIR-VCG assesses this bundle manifest against FAIR, ARRIVE and MNMS.

## Security boundaries

- FAIR-VCG receives generated assessment CSVs, not DVC credentials.
- The DVC API key is never sent to FAIR-VCG.
- Full time-series values are not sent during post-analysis assessment; the
  provider receives the generated bundle manifest.
- Approval identity and timestamps come from the authenticated OpenScientist API,
  not from agent-supplied fields.

## Remaining deployment work

The adapter and mock-contract tests are implemented. The remaining work is
deployment and live acceptance:

- [ ] Deploy the pinned FAIR-VCG revision.
- [ ] Make `FAIR_PREPARE_URL` reachable from each per-job agent container.
- [ ] Verify Docker DNS/network forwarding or the internal HTTPS route in the
  actual deployment configuration.
- [ ] Add service readiness and version checks.
- [ ] Exercise upload, metadata, FAIR score and template application from the
  built agent image.
- [ ] Verify that upstream errors remain redacted in application and MCP logs.
- [ ] Complete a live pre- and post-analysis assessment against a bounded
  Tecniplast validation dataset.

A deployment can place both services on the same private Docker network or
expose FAIR-VCG through an internal HTTPS endpoint. FAIR-VCG availability must
fail closed before approval-dependent analysis proceeds.
