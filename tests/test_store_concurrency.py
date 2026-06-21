"""
Concurrency regression test for MemoryStore.

The daemon shares one sqlite3 connection across the asyncio main thread and
several executor threads (capture, refine, retention, sensitivity re-check).
Before MemoryStore serialized its methods, concurrent access raised thousands
of "cannot start a transaction within a transaction" / "bad parameter or other
API misuse" errors and could corrupt writes. This test reproduces that access
pattern and asserts it now runs cleanly.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.store import MemoryStore
from src.turboquant import encoder as tq


def _cv():
    v = np.random.default_rng().normal(size=384).astype(np.float32)
    v /= np.linalg.norm(v)
    return tq.encode(v)


class TestStoreConcurrency(unittest.TestCase):

    def test_concurrent_readers_and_writers_do_not_error(self):
        store = MemoryStore(Path(tempfile.mkdtemp()) / "m.db")
        errors: list[str] = []
        stop = threading.Event()

        def insert():
            i = 0
            while not stop.is_set():
                try:
                    c = _cv()
                    store.insert_memory(f"cap {i} {time.time()}", "win", "App",
                                        c, c.residual_norm, 0)
                    i += 1
                except Exception as e:  # noqa: BLE001
                    errors.append(f"insert: {e!r}")

        def prune():
            while not stop.is_set():
                try:
                    store.prune_older_than(time.time() + 1)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"prune: {e!r}")
                time.sleep(0.005)

        def delete():
            while not stop.is_set():
                try:
                    rows = store.get_recent(limit=1)
                    if rows:
                        store.delete_memory(rows[0]["id"])
                except Exception as e:  # noqa: BLE001
                    errors.append(f"delete: {e!r}")
                time.sleep(0.003)

        def config():
            while not stop.is_set():
                try:
                    store.set_config("k", str(time.time()))
                    store.get_config("k")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"config: {e!r}")
                time.sleep(0.002)

        threads = [threading.Thread(target=f, daemon=True)
                   for f in (insert, prune, delete, config, insert)]
        for t in threads:
            t.start()
        time.sleep(2.5)
        stop.set()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [], f"{len(errors)} concurrency errors, e.g. {errors[:3]}")
        # Store is still usable afterward.
        self.assertIsInstance(store.get_memory_count(), int)


if __name__ == "__main__":
    unittest.main()
