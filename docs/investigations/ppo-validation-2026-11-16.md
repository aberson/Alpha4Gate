# PPO P2 validation receipt

Date: 2026-11-16. Documentation-only change; no game-strength claim.

| Check | Selection / command | Result |
|---|---|---|
| Focused tests plus quick repository invariant | `.venv/Scripts/python.exe -m pytest tests/test_features_z.py tests/test_neural_engine.py tests/test_repo_hygiene.py -q -p no:cacheprovider --basetemp $env:TEMP/alpha4gate-direction-ppo-pytest` | Exit 0; **36 passed in 13.90s** |
| Compile without importing or writing bytecode | Python `compile(Path(name).read_bytes(), name, 'exec')` for `bots/v13/learning/{neural_engine,features,trainer,environment}.py` and `bots/v13/runner.py` | Exit 0; 5 files compiled in memory |
| Patch whitespace | `git diff --check -- docs/seeds/ppo-direction.md` | Exit 0; Git emitted only its LF-to-CRLF advisory |
| Checkpoint static precheck | Python `zipfile.ZipFile`, `json.loads(archive.read('data'))`; inspect observation `_shape`, action `n`, `num_timesteps` | v15: [55], 6, 23040; v0_pretrain: [55], 6, 0. No serialized object execution |

`test_features_z.py` resolves the current tree (v13 at inspection); the neural-engine
suite imports **v0**, so its pass is supporting regression evidence, not v13 inference
acceptance. `test_repo_hygiene.py` is the quick repository identity invariant. Source
inspection anchors the v13-specific statements. No frontend, full suite, real-checkpoint
inference, WSL runtime, scoreboard or SC2 test ran. Those remain future acceptance work.

The report links primary external sources directly and labels proposed experiments,
budgets, dispositions and unresolved choices. Canonical plan status is unchanged.
