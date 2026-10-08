import unittest

from mini_redis.store import OutOfMemoryError, Store


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = Store(self.clock)

    def assert_consistent(self):
        store = self.store
        self.assertEqual(store._entries.size, store._lru.size)
        memory = 0
        expiries = 0
        for pair in store._entries.items():
            entry = pair.value
            memory += len(entry.key.encode("utf-8")) + len(entry.value.encode("utf-8"))
            self.assertIs(entry.lru_node.data, entry)
            if entry.expiry is not None:
                expiries += 1
                self.assertIs(entry.expiry.value, entry)
        self.assertEqual(memory, store.used_memory)
        self.assertEqual(expiries, store._expiry.size)

    def test_subject_memory_and_lru_example(self):
        store = self.store
        store.configure_maxmemory(30)
        store.set("user:1", "Alice")
        store.set("user:2", "Bob")
        store.set("user:3", "Charlie")
        self.assertIsNone(store.get("user:1"))
        self.assertEqual(store.memory_info(), (22, 30, 1))
        self.assert_consistent()

    def test_get_refreshes_lru_but_exists_and_ttl_do_not(self):
        store = self.store
        store.configure_maxmemory(4)
        store.set("a", "1")
        store.set("b", "2")
        store.get("a")
        store.exists("b")
        store.ttl("b")
        store.set("c", "3")
        self.assertEqual(store.exists("b"), 0)
        self.assertEqual(store.exists("a"), 1)
        self.assert_consistent()

    def test_utf8_and_replacement_sizes(self):
        store = self.store
        store.set("키", "값")
        self.assertEqual(store.used_memory, 6)
        store.set("키", "x")
        self.assertEqual(store.used_memory, 4)
        self.assertEqual(store.dbsize(), 1)
        self.assertEqual(store.delete("키"), 1)
        self.assertEqual(store.delete("키"), 0)
        self.assertEqual(store.used_memory, 0)
        self.assert_consistent()

    def test_oom_preserves_existing_value_ttl_and_lru(self):
        store = self.store
        store.configure_maxmemory(6)
        store.set("a", "1")
        store.set("b", "2")
        store.expire("a", 10)
        before = tuple(entry.key for entry in store._lru)
        with self.assertRaises(OutOfMemoryError):
            store.set("a", "too large")
        self.assertEqual(tuple(entry.key for entry in store._lru), before)
        self.assertEqual(store.ttl("a"), 10)
        self.assertEqual(store.get("a"), "1")
        self.assertEqual(store.memory_info(), (4, 6, 0))
        self.assert_consistent()

    def test_lowering_limit_evicts_multiple_keys_on_next_set(self):
        store = self.store
        for key in ("a", "b", "c"):
            store.set(key, "1")
        store.configure_maxmemory(2)
        self.assertEqual(store.dbsize(), 3)
        store.set("d", "1")
        self.assertEqual(tuple(store.keys()), ("d",))
        self.assertEqual(store.evicted_keys, 3)
        store.configure_maxmemory(0)
        store.set("large", "x" * 100)
        self.assertEqual(store.dbsize(), 2)
        self.assert_consistent()

    def test_expiry_is_removed_from_all_structures_without_eviction_count(self):
        store = self.store
        store.set("a", "value")
        store.expire("a", 3)
        self.clock.now += 2.5
        self.assertEqual(store.ttl("a"), 0)
        self.clock.now += 0.5
        self.assertEqual(store.dbsize(), 0)
        self.assertIsNone(store.get("a"))
        self.assertEqual(store.ttl("a"), -2)
        self.assertEqual(store.expire("a", 3), 0)
        self.assertEqual(tuple(store.keys()), ())
        self.assertEqual(store.memory_info(), (0, 0, 0))
        self.assert_consistent()

    def test_expire_reschedule_changes_heap_order_without_stale_items(self):
        store = self.store
        store.set("a", "1")
        store.set("b", "2")
        store.expire("a", 2)
        store.expire("b", 3)
        store.expire("a", 5)
        self.assertEqual(store._expiry.size, 2)
        self.assertEqual(store.next_expiry_delay(), 3)
        self.clock.now += 3
        self.assertEqual(store.exists("b"), 0)
        self.assertEqual(store.exists("a"), 1)
        store.expire("a", 1)
        self.clock.now += 1
        self.assertEqual(store.exists("a"), 0)
        self.assert_consistent()

    def test_set_and_delete_clear_ttl_before_recreating_key(self):
        store = self.store
        store.set("a", "old")
        store.expire("a", 1)
        store.set("a", "new")
        self.assertEqual(store.ttl("a"), -1)
        self.assertEqual(store._expiry.size, 0)
        store.expire("a", 1)
        store.delete("a")
        store.set("a", "recreated")
        self.clock.now += 2
        self.assertEqual(store.get("a"), "recreated")
        self.assert_consistent()

    def test_nonpositive_expiry_and_eviction_clear_heap(self):
        store = self.store
        store.set("a", "1")
        self.assertEqual(store.expire("a", 0), 1)
        store.set("a", "1")
        store.expire("a", 10)
        store.configure_maxmemory(2)
        store.set("b", "2")
        self.assertEqual(store._expiry.size, 0)
        self.assertEqual(store.evicted_keys, 1)
        self.assert_consistent()


if __name__ == "__main__":
    unittest.main()
