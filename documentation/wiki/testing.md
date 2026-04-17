# Testing

What's tested, how to run it, and what's not covered.

> **At a glance:** 1020 unit tests across 51 files, collected in ~9s. Heavy mocking of SC2 BotAI via MagicMock/AsyncMock — no SC2 client needed for unit tests. Integration tests require a running SC2 client and are marked `@pytest.mark.sc2`. No `conftest.py` — fixtures are inline. The test suite now covers the full autonomous loop (daemon, promotion, rollback, evaluator, advised-run bridge).

## Purpose & Design

Tests are organized one file per module. Unit tests mock the SC2 API boundary so they run fast and deterministically. Integration tests that need a live SC2 client are opt-in via marker.

> Note: this doc covers the **code-level unit test** layer. Don't confuse it with the TEST phase of the outer learning loop (see [improve-bot-advised-architecture.md](improve-bot-advised-architecture.md)) — that's game-playing validation of a behavioral fix, not unit tests.

### Running tests

```bash
uv run pytest                    # All unit tests (no SC2 needed)
uv run pytest -m sc2             # SC2 integration tests (SC2 must be running)
uv run ruff check .              # Lint
uv run mypy src                  # Type check (strict mode)
cd frontend && npx tsc --noEmit  # TypeScript check
cd frontend && npm test          # Frontend tests (vitest)
```

### Test distribution — bot (the task)

| File | Module tested |
|------|--------------|
| test_executor.py | commands/executor |
| test_commands.py | commands (parsing + integration) |
| test_interpreter.py | Claude Haiku command interpreter |
| test_army_coherence.py | ArmyCoherenceManager |
| test_build_orders.py | BuildOrder, BuildSequencer |
| test_build_backlog.py | BuildBacklog retry queue |
| test_scouting.py | ScoutManager, threat assessment |
| test_decision_engine.py | StrategicState transitions |
| test_micro.py | MicroController, target selection |
| test_claude_advisor.py | ClaudeAdvisor async + parsing |
| test_macro_manager.py | MacroManager economy checks |
| test_fortification.py | FortificationManager scaling |
| test_observer.py | observe() snapshot extraction |
| test_logger.py | GameLogger JSONL output |
| test_api.py | FastAPI endpoints |
| test_api_commands.py | Command API integration |
| test_web_socket.py | WebSocket queue/broadcast |
| test_bot_coherence.py | Bot coherence integration |
| test_bot_fortify.py | Bot fortification integration |
| test_bot_transitions.py | Bot state-transition recording |
| test_connection.py | SC2 launcher |
| test_replay_parser.py | Replay parsing |
| test_runner.py | Game runner lifecycle |
| test_config.py | Configuration loading |
| test_console.py | Console formatting |
| test_audit_log.py | Decision audit log persistence |

### Test distribution — learning & loop

| File | Module tested |
|------|--------------|
| test_trainer.py | TrainingOrchestrator |
| test_daemon.py | TrainingDaemon — triggers, cycles, watchdog |
| test_environment.py | SC2Env gymnasium wrapper |
| test_environment_teardown.py | Episode teardown contract (#72 regression) |
| test_rewards.py | RewardCalculator rule evaluation |
| test_reward_aggregator.py | Per-rule trend aggregation for dashboard |
| test_checkpoints.py | Checkpoint save/load/prune/promote |
| test_database.py | TrainingDB (SQLite) |
| test_database_threadsafety.py | DB thread-safety guards |
| test_features.py | Feature encoding (24-dim vector) |
| test_neural_engine.py | NeuralDecisionEngine inference |
| test_rules_policy.py | Rule-based policy reference (for KL targets) |
| test_ppo_kl.py | PPO-with-KL-to-rules variant |
| test_imitation.py | Imitation learning — behavior cloning |
| test_imitation_init.py | Imitation-init path for PPO warm-start |
| test_evaluator.py | ModelEvaluator — including crash-game handling (#67) |
| test_evaluator_db_roundtrip.py | Game-ID regression guard (#84 / Phase 4.7 Step 1) |
| test_promotion.py | PromotionManager gate logic |
| test_rollback.py | RollbackMonitor regression detection |
| test_advisor_bridge.py | Thread-safe advisor bridge for training |
| test_error_log_buffer.py | ErrorLogBuffer ring buffer |
| test_batch_runner.py | GameRecord aggregation |
| test_ladder.py | Elo ladder, match recording, promotion gate |
| test_sandbox_hook.py | Pre-commit sandbox enforcement (9 cases: passthrough, allowed/forbidden paths, traversal, mixed) |

Plus `pyproject.toml` config for pytest, mypy, ruff.

### What's well-tested

- **Command pipeline** — parser, interpreter, executor, queue, recipes
- **Reward system** — rule evaluation, terminal rewards, edge cases
- **Army coherence** — parameter randomization, attack/retreat decisions
- **Decision engine** — all state transitions, overrides, edge cases
- **Inner loop (new since last doc pass)** — promotion gate, rollback monitor, evaluator crash handling, daemon triggers + watchdog
- **Environment teardown** — regression-guarded #72 blocker (episode-exit contract) in `test_environment_teardown.py`
- **Game-ID round-trip** — regression-guarded #84 in `test_evaluator_db_roundtrip.py`

### What's thin

- **Runner lifecycle** — single test; relies on SC2 integration tests for coverage
- **Replay parsing** — basic parsing only; no fidelity tests against full replays
- **Bot integration** — on_step() pipeline tested indirectly through component tests
- **Frontend Python-side contract** — no end-to-end HTTP→WS integration tests for the dashboard
- **Advised-run skill itself** — no code-level tests; validated by actually running `/improve-bot-advised` (the run logs are the evidence)

### Testing patterns

- **No conftest.py.** All fixtures are inline within test files using class-scoped setup or function-level MagicMock construction.
- **SC2 mocking:** BotAI is replaced with MagicMock. Unit lists, structure lists, mineral counts, and game state are all mock properties.
- **Async mocking:** AsyncMock for async methods (on_step, execute, Claude API calls).
- **Deterministic seeds:** ArmyCoherenceManager tests use fixed seeds for reproducible parameter rolls.
- **DB tests:** use tmp_path for isolated SQLite files; no shared state between tests.
- **Thread-safety tests:** `test_database_threadsafety` and `test_advisor_bridge` spawn real threads to exercise the queue-based bridges.

---

## Implementation Notes

**SC2 integration tests:** Marked with `@pytest.mark.sc2`. Require StarCraft II installed and running. Test actual game launch, bot initialization, and basic gameplay. Not run in CI.

**Type checking:** mypy strict mode (`disallow_untyped_defs`, `no_implicit_optional`). All functions require type annotations.

**Linting:** ruff with rules E, F, I, UP, B. Line length 100.

**Frontend tests:** vitest (~126 tests). Run via `cd frontend && npm test`.

| File | Purpose |
|------|---------|
| `tests/test_*.py` | 48 Python test modules |
| `frontend/src/**/*.test.{ts,tsx}` | Frontend tests (vitest) |
| `pyproject.toml` | pytest, mypy, ruff configuration |
