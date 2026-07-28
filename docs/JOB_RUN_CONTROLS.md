# Job Run Controls

OpenScientist job owners can change the course of an active investigation
without discarding the evidence it has already persisted.

## Controls

### Pause and resume

Pause records the current iteration as the first unfinished iteration, changes
the job status to `paused`, and stops the agent container. A paused job remains
paused across application restarts and does not consume an agent container.

Resume starts a fresh agent container and continues from the recorded
unfinished iteration. Because pause is immediate, work that was only in the
stopped model turn may not have been persisted. OpenScientist reruns that
iteration on resume; findings, literature, plots, and summaries saved before
the pause remain available.

### Reduce the iteration limit

The maximum iteration count can be reduced for jobs that are pending, queued,
running, paused, or awaiting feedback. The limit cannot be increased through
this control and cannot be set below the current iteration.

A running agent reads the latest limit at safe checkpoints between model
turns. Reducing the limit to the current iteration lets the current turn
finish, skips subsequent discovery turns, and proceeds to normal report
generation.

### Stop discovery and generate a report

Stop and report immediately ends the discovery container and starts a fresh
report-only container. The report uses the persisted knowledge state, including
findings, literature, hypotheses, analyses, and plots saved before the stop.
Unsaved text from the interrupted model turn cannot be recovered.

This differs from cancellation: cancellation stops the job without producing a
new report.

## Status transitions

```text
running / awaiting_feedback
    ├── pause ───────────────> paused ── resume ──> pending / queued / running
    ├── stop and report ─────> generating_report ──> completed / failed
    └── reduce iterations ───> running ─────────────> generating_report

paused
    ├── stop and report ─────> generating_report ──> completed / failed
    └── cancel ──────────────> cancelled
```

## HTTP API

All endpoints require authentication and job ownership.

| Method | Endpoint | Effect |
| --- | --- | --- |
| `POST` | `/api/v1/jobs/{job_id}/pause` | Pause an active job |
| `POST` | `/api/v1/jobs/{job_id}/resume` | Resume a paused job |
| `PATCH` | `/api/v1/jobs/{job_id}/iterations` | Reduce `max_iterations` |
| `POST` | `/api/v1/jobs/{job_id}/stop-and-report` | Stop discovery and report from persisted work |
| `POST` | `/api/v1/jobs/{job_id}/cancel` | Cancel without generating a report |

The iteration update body is:

```json
{
  "max_iterations": 4
}
```

## Persistence and safety

- `paused` is a durable job status.
- `resume_iteration` identifies the first unfinished iteration.
- Terminal states clear `resume_iteration`.
- A resume always uses a new container and resets the model session.
- Only completed tool writes and saved iteration artifacts are guaranteed to
  survive an immediate pause or stop.
