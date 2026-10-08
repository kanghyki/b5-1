import random
import unittest

from mini_redis.structures.hash_map import HashMap
from mini_redis.structures.linked_list import DoublyLinkedList
from mini_redis.structures.min_heap import MinHeap


class LinkedListTests(unittest.TestCase):
    def test_node_moves_and_removals_preserve_links(self):
        linked = DoublyLinkedList()
        first = linked.insert_front("first")
        middle = linked.insert_back("middle")
        last = linked.insert_back("last")
        linked.move_to_front(last)
        self.assertEqual(tuple(linked), ("last", "first", "middle"))
        self.assertEqual(linked.remove_node(first), "first")
        self.assertEqual(linked.remove_front(), "last")
        self.assertEqual(linked.remove_back(), "middle")
        self.assertEqual(linked.size, 0)
        self.assertIs(linked._head.next, linked._tail)
        self.assertIs(linked._tail.prev, linked._head)
        with self.assertRaises(ValueError):
            linked.remove_node(middle)
        with self.assertRaises(IndexError):
            linked.remove_front()

    def test_foreign_node_does_not_corrupt_lists(self):
        first, second = DoublyLinkedList(), DoublyLinkedList()
        node = first.insert_back(1)
        with self.assertRaises(ValueError):
            second.move_to_front(node)
        self.assertEqual(tuple(first), (1,))
        self.assertEqual(second.size, 0)


class HashMapTests(unittest.TestCase):
    def test_collisions_updates_and_growth(self):
        class CollisionMap(HashMap):
            @staticmethod
            def hash_key(key):
                return 0

        mapping = CollisionMap(4)
        for index in range(100):
            mapping.put(str(index), index)
        self.assertEqual(mapping.size, 100)
        self.assertGreaterEqual(mapping.capacity, 134)
        for index in range(100):
            self.assertEqual(mapping.get(str(index)), index)
        mapping.put("50", None)
        self.assertTrue(mapping.contains("50"))
        self.assertEqual(mapping.size, 100)
        for index in range(0, 100, 2):
            self.assertTrue(mapping.remove(str(index)))
            self.assertFalse(mapping.remove(str(index)))
        self.assertEqual(mapping.size, 50)
        self.assertEqual(len(tuple(mapping.keys())), 50)

    def test_resize_threshold_and_unicode_keys(self):
        mapping = HashMap(4)
        for key in ("가", "나", "다"):
            mapping.put(key, key)
        self.assertEqual(mapping.capacity, 4)
        mapping.put("라", "라")
        self.assertEqual(mapping.capacity, 8)
        self.assertEqual(mapping.get("가"), "가")


class HeapTests(unittest.TestCase):
    def test_random_priority_updates_and_arbitrary_removals(self):
        randomizer = random.Random(42)
        heap = MinHeap()
        # 고정 크기 참조 배열은 힙 위치 이동 검증을 위한 테스트 입력이다.
        handles = [None] * 150
        for index in range(150):
            handles[index] = heap.push(randomizer.randrange(1000), index)
        for index in range(0, 150, 3):
            heap.update(handles[index], randomizer.randrange(1000))
        for index in range(0, 150, 5):
            heap.remove(handles[index])
        previous = -1
        while heap.size:
            item = heap.pop()
            self.assertGreaterEqual(item.priority, previous)
            self.assertEqual(item.index, -1)
            previous = item.priority
        with self.assertRaises(ValueError):
            heap.update(handles[0], 1)
        with self.assertRaises(IndexError):
            heap.peek()


if __name__ == "__main__":
    unittest.main()
