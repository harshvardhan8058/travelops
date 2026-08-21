# Prompts

One versioned file per reasoning agent: `planner.v1.md`, `explainer.v1.md`,
`report.v1.md`. Never inline prompt strings in Python.

Rules:
- The planner receives typed fields, never raw external text. Retrieved legal text is
  display context for the explainer, never an instruction channel.
- Every prompt states the exact JSON payload shape expected, matching
  `app/agents/contract.py`. Output is validated before use; malformed output is rejected
  and retried, then falls back to the deterministic playbook.
- A prompt change means a new version file, because `plan.prompt_version` is recorded on
  every plan for reproducibility.

Owner: Stream A.
