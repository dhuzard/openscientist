# Governed two-cage DVC evidence demo

This fixture is a deliberately synthetic positive control for the strict HCMO
evidence path. It complements the real-job negative control documented in
[`docs/experiments/HCMO_DVC_LOCAL_TRIAL.md`](../../docs/experiments/HCMO_DVC_LOCAL_TRIAL.md).
No metadata from the real job are reused or inferred here.

The fixture contains two physical cages, two subjects, two sensors, two
observations, paired event files, explicit per-cage schedules/timezones,
approved trace mappings, housing intervals, enclosure dimensions, and
first-class finding-to-analysis/data/result links. Cage A is sampled every
minute and cage B every five minutes. The tiny analysis first averages within
each cage's five-minute bins, then averages bins within cage, then gives both
cages equal weight. It is a structural demonstration, not a circadian study.

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m openscientist.evidence.dvc_job_readiness `
  --job-dir examples/hcmo_dvc_demo/hcmo-dvc-governed-demo `
  --governance-manifest examples/hcmo_dvc_demo/governance-manifest.json `
  --relational-inventory examples/hcmo_dvc_demo/relational-inventory.json `
  --output-dir examples/hcmo_dvc_demo/results/readiness

.\.venv\Scripts\python.exe -m openscientist.evidence.hcmo_export `
  --snapshot examples/hcmo_dvc_demo/job-snapshot.json `
  --source-root examples/hcmo_dvc_demo/hcmo-dvc-governed-demo `
  --report examples/hcmo_dvc_demo/hcmo-dvc-governed-demo/final_report.md `
  --output-dir examples/hcmo_dvc_demo/results/evidence
```

The preflight exits zero only when all 18 required checks execute, with the
mixed-cadence condition retained as `WARN`. The exporter then verifies the
source bytes and generates SHACL- and closed-vocabulary-validated RDF plus a
graph-derived traceability appendix. Generated `results/` are ignored.

