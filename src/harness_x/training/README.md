# Grounded self-model curriculum

Milestone 12 generates self-model training/evaluation records from Harness X ground truth.

Labels may come only from:

- deterministic system rules and active configuration;
- known fault injections;
- known interventions with deterministic before/after relations.

Teacher-model answers are not used as labels.

Each record keeps its scenario seed, architecture family, source system/state fingerprint, expected structured decision, accepted alternatives, structured rationale metadata, and label source. Evaluation seeds are separate from training seeds. The default split policy also holds out complete diagnostic fault families rather than randomly splitting their rows.

The generated files are:

- `train.jsonl`
- `eval.jsonl`
- `manifest.json`

The manifest records split seed IDs, held-out fault families, per-family counts, and a dataset content fingerprint.
