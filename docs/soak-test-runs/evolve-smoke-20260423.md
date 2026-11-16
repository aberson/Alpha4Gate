# Evolve run — 2026-04-23T22:53:46+00:00

- Parent (start): `v0`
- Parent (end): `v4`
- Wall-clock budget: 0.0h
- Started: 2026-04-23T22:53:46+00:00
- Finished: 2026-04-24T03:44:41+00:00
- Generations completed: 12
- Generations promoted: 4
- Total evictions: 9
- Stop reason: pool-exhausted

## Generations

| gen | fitness pass/close/fail | stack-apply | regression | outcome |
|---|---|---|---|---|
| 1 | 2/0/0 | stack-apply-pass | pass | stack-apply-pass → promoted v1 |
| 2 | 0/0/2 | no winners | none | no winners |
| 3 | 1/1/0 | stack-apply-pass | pass | stack-apply-pass → promoted v2 |
| 4 | 0/1/1 | no winners | none | no winners |
| 5 | 0/2/0 | no winners | none | no winners |
| 6 | 0/1/1 | no winners | none | no winners |
| 7 | 1/1/0 | stack-apply-pass | pass | stack-apply-pass → promoted v3 |
| 8 | 1/0/1 | stack-apply-pass | pass | stack-apply-pass → promoted v4 |
| 9 | 2/0/0 | stack-apply-pass | rollback | stack-apply-pass → ROLLBACK |
| 10 | 0/1/1 | no winners | none | no winners |
| 11 | 0/1/1 | no winners | none | no winners |
| 12 | 0/0/0 (+1 crash) | no winners | none | no winners |
