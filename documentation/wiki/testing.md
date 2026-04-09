# Testing

What's tested, how to run it, and what's not covered.

> **At a glance:** 500 unit tests across 32 files. Heavy mocking of SC2 BotAI via
> MagicMock/AsyncMock — no SC2 client needed for unit tests. Integration tests require
> a running SC2 client and are marked with `@pytest.mark.sc2`. No conftest.py; fixtures
> are inline. Core test clusters: executor (38), commands (35), rewards (33),
> army_coherence (32), decision_engine (24).

## Purpose & Design

Tests are organized one file per module. Unit tests mock the SC2 API boundary so they
run fast and deterministically. Integration tests that need a live SC2 client are
opt-in via marker.

### Running tests

```bash
uv run pytest                    # 500 unit tests (~7s, no SC2 needed)
uv run pytest -m sc2             # SC2 integration tests (SC2 must be running)
uv run ruff check .              # Lint
uv run mypy src                  # Type check (strict mode)
cd frontend && npx tsc --noEmit  # TypeScript check
```

### Test distribution

| File | Module tested | Tests |
|------|--------------|-------|
| test_executor.py | commands/executor | 38 |
| test_commands.py | commands (parsing + integration) | 35 |
| test_rewards.py | RewardCalculator | 33 |
| test_army_coherence.py | ArmyCoherenceManager | 32 |
| test_build_orders.py | BuildOrder, BuildSequencer | 27 |
| test_scouting.py | ScoutManager, threat assessment | 25 |
| test_decision_engine.py | StrategicState transitions | 24 |
| test_interpreter.py | Claude Haiku command interpreter | 23 |
| test_micro.py | MicroController, target selection | 22 |
| test_api.py | FastAPI endpoints | 21 |
| test_claude_advisor.py | ClaudeAdvisor async + parsing | 20 |
| test_macro_manager.py | MacroManager economy checks | 18 |
| test_fortification.py | FortificationManager scaling | 18 |
| test_api_commands.py | Command API integration | 15 |
| test_batch_runner.py | GameRecord aggregation | 14 |
| test_replay_parser.py | Replay parsing | 14 |
| test_trainer.py | TrainingOrchestrator | 14 |
| test_bot_coherence.py | Bot coherence integration | 13 |
| test_checkpoints.py | Checkpoint save/load/prune | 12 |
| test_observer.py | observe() snapshot extraction | 11 |
| test_database.py | TrainingDB (SQLite) | 10 |
| test_environment.py | SC2Env gymnasium wrapper | 10 |
| test_features.py | Feature encoding | 9 |
| test_neural_engine.py | NeuralDecisionEngine inference | 8 |
| test_web_socket.py | WebSocket queue/broadcast | 6 |
| test_bot_fortify.py | Bot fortification integration | 4 |
| test_imitation.py | Imitation learning | 4 |
| test_logger.py | GameLogger JSONL output | 4 |
| test_config.py | Configuration loading | 3 |
| test_console.py | Console formatting | 2 |
| test_runner.py | Game runner lifecycle | 1 |

### What's well-tested

- **Command pipeline** (73 tests) — parser, interpreter, executor, queue, recipes
- **Reward system** (33 tests) — rule evaluation, terminal rewards, edge cases
- **Army coherence** (32 tests) — parameter randomization, attack/retreat decisions
- **Decision engine** (24 tests) — all state transitions, overrides, edge cases

### What's thin

- **Environment** (10 tests) — threading model has limited coverage
- **Imitation** (4 tests) — basic training loop only
- **Logger** (4 tests) — file I/O tested, not thread safety
- **Bot integration** (17 tests across 2 files) — complex on_step() pipeline
  mostly tested indirectly through component tests
- **Frontend** — no JavaScript tests

### Testing patterns

- **No conftest.py.** All fixtures are inline within test files using class-scoped
  setup or function-level MagicMock construction.
- **SC2 mocking:** BotAI is replaced with MagicMock. Unit lists, structure lists,
  mineral counts, and game state are all mock properties. This lets tests run
  without SC2 installed.
- **Async mocking:** AsyncMock for async methods (on_step, execute, Claude API calls).
- **Deterministic seeds:** ArmyCoherenceManager tests use fixed seeds for reproducible
  parameter rolls.

---

## Implementation Notes

**SC2 integration tests:** Marked with `@pytest.mark.sc2`. Require StarCraft II
installed and running. Test actual game launch, bot initialization, and basic gameplay.
Not run in CI (no SC2 on CI machines).

**Type checking:** mypy strict mode (`disallow_untyped_defs`, `no_implicit_optional`).
All functions require type annotations.

**Linting:** ruff with rules E, F, I, UP, B. Line length 100.

| File | Purpose |
|------|---------|
| `tests/test_*.py` | 32 test modules |
| `pyproject.toml` | pytest, mypy, ruff configuration |
