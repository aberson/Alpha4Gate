"""Thread-safety regression tests for ``TrainingDB``.

Phase 4.5 Step 5 blocker #66: ``store_game`` and ``store_transition`` are
called from per-game worker threads spawned by the SC2 environment, but the
SQLite connection used to be created with the default
``check_same_thread=True``. That caused
``sqlite3.ProgrammingError: SQLite objects created in a thread can only be
used in that same thread.`` and dropped ~30% of eval-game rows during soak
run #1.

These tests spin up multiple worker threads that hammer the write paths
concurrently. They MUST fail on the unfixed code (cross-thread crash) and
PASS once ``check_same_thread=False`` + an internal lock are in place.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from alpha4gate.learning.database import TrainingDB
from alpha4gate.learning.features import BASE_GAME_FEATURE_DIM as FEATURE_DIM


class TestThreadSafety:
    """Phase 4.5 Step 5 blocker #66 regression tests."""

    def test_store_game_is_thread_safe(self, tmp_path: Path) -> None:
        """8 threads x 10 store_game calls each must land all 80 rows."""
        db = TrainingDB(tmp_path / "threadsafe_games.db")
        try:
            n_threads = 8
            per_thread = 10

            def worker(thread_idx: int) -> None:
                for i in range(per_thread):
                    db.store_game(
                        game_id=f"t{thread_idx}-g{i}",
                        map_name="Simple64",
                        difficulty=1,
                        result="win" if i % 2 == 0 else "loss",
                        duration_secs=300.0,
                        total_reward=1.0,
                        model_version="v0",
                    )

            with ThreadPoolExecutor(max_workers=n_threads) as pool:
                futures = [pool.submit(worker, t) for t in range(n_threads)]
                for fut in as_completed(futures):
                    # ``fut.result()`` re-raises any exception the worker
                    # raised — it is the sole exception channel for this
                    # test. No side-channel error list is needed.
                    fut.result()

            assert db.get_game_count() == n_threads * per_thread
        finally:
            db.close()

    def test_store_transition_is_thread_safe(self, tmp_path: Path) -> None:
        """4 threads x 5 store_transition calls each must land all 20 rows."""
        db = TrainingDB(tmp_path / "threadsafe_transitions.db")
        try:
            # Single shared parent game; transitions reference it via FK.
            db.store_game(
                game_id="shared-game",
                map_name="Simple64",
                difficulty=1,
                result="win",
                duration_secs=300.0,
                total_reward=1.0,
                model_version="v0",
            )

            n_threads = 4
            per_thread = 5
            errors: list[BaseException] = []
            errors_lock = threading.Lock()

            def worker(thread_idx: int) -> None:
                try:
                    for i in range(per_thread):
                        state = np.zeros(FEATURE_DIM, dtype=np.float32)
                        db.store_transition(
                            game_id="shared-game",
                            # Unique step_index per (thread, i) pair.
                            step_index=thread_idx * per_thread + i,
                            game_time=float(i),
                            state=state,
                            action=0,
                            reward=0.0,
                        )
                except BaseException as exc:  # noqa: BLE001 - capture for assert
                    with errors_lock:
                        errors.append(exc)

            threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
            for th in threads:
                th.start()
            for th in threads:
                th.join()

            assert errors == [], f"worker threads raised: {errors!r}"
            assert db.get_transition_count() == n_threads * per_thread
        finally:
            db.close()
