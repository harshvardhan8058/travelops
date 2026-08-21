# data/generators — synthetic dataset

Owner: **Stream C**. Deterministic generators, fixed seed **20260807**.

Work **backwards from the targets** in `data/fixtures/bengaluru_storm.yaml`. The generator exists
to satisfy those numbers, not the other way round:

| Target | Value |
| --- | --- |
| Affected flights | 8 |
| Passengers | ~604 |
| At-risk connections | 22 |
| Candidate hotels | 11 |
| Traceable crew pairings | **exactly 9** |

## The pairing generator is the hard part

Model it explicitly as `pairing → pairing_leg → flight`, with each leg's role being `operating` or
`positioning`. A flat crew-to-flight column cannot support the claim that eight flights affect nine
rotations, which is the single most scrutinised number in the demo.

Each affected pairing must be attributable to exactly one mechanism: `operating`, `onward_duty`,
`second_pairing` or `positioning`. That mechanism becomes the edge label in the cascade graph.

**Write the assertion that counts 9 pairings before writing the generator that satisfies it.**

## Other requirements

- Passengers are visibly synthetic on inspection: `PAX-00001`, `@example.com`. No code path stores
  real personal data.
- Make hotel capacity deliberately insufficient for at least one allocation, so partial allocation
  and prioritisation are actually exercised rather than assumed.
- `make seed` must produce a byte-identical dataset for the same seed. Commit the dump; never
  regenerate during a demo.
