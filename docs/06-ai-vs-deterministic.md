# 6. AI vs Deterministic: The Central Decision

## The hardest problem is not coding

It is deciding:

> **What should the AI decide, and what should remain deterministic?**

Get this boundary right and the system is predictable and trustworthy. Get it wrong and you have
either a chatbot pretending to be infrastructure, or a rules engine with a model bolted on for show.

## The split

| Task | AI | Code |
| --- | :---: | :---: |
| Understand disruption | ✅ | |
| Plan recovery | ✅ | |
| Generate passenger message | ✅ | |
| Explain why a plan was chosen | ✅ | |
| Calculate compensation | | ✅ |
| Filter available hotels | | ✅ |
| Sort flights by delay | | ✅ |
| Book hotel (simulated) | | ✅ |

## Why the boundary falls there

The AI column contains tasks that are **open-ended, contextual, or linguistic**. There is no single
correct recovery plan; there are trade-offs between cost, passenger impact, and crew legality that
need weighing. Similarly, there is no algorithm for a well-worded apology.

The code column contains tasks with **exactly one correct answer**. Compensation is a function of
regulation and delay duration — if a model computes it, it can be wrong, and being wrong is a legal
and financial liability. Sorting is sorting. Filtering is a `WHERE` clause.

## The test to apply to any new feature

Ask, in order:

1. **Is there one provably correct answer?** → Code. Always.
2. **Is it a lookup, filter, sort, or arithmetic?** → Code.
3. **Is it governed by a regulation or a business rule?** → Code. Rules change via config, not prompts.
4. **Does it require weighing incompatible options, or producing natural language?** → AI.
5. **Would a wrong answer be unrecoverable?** → Code, or AI with a mandatory human approval step.

## Consequences of this discipline

- **Predictability.** The same disruption produces the same plan shape every run — essential for a
  live demo.
- **Testability.** Deterministic nodes get ordinary unit tests. You cannot meaningfully unit-test a
  paragraph of generated prose.
- **Trust.** When a judge or an operations manager asks why compensation was ₹4,200, the answer is a
  line of code and a regulation, not "the model decided".
- **Cost.** Most of the workload never touches the LLM, so rate limits stop being a design
  constraint.
