"""키 저장소의 LRU·TTL·UTF-8 메모리 계산을 한 곳에서 관리한다."""

import time

from .structures.hash_map import HashMap
from .structures.linked_list import DoublyLinkedList
from .structures.min_heap import MinHeap


class OutOfMemoryError(Exception):
    """단일 엔트리가 maxmemory를 초과하여 저장할 수 없다."""


class Entry:
    def __init__(self, key, value, byte_size):
        self.key, self.value, self.byte_size = key, value, byte_size
        self.lru_node = None
        self.expiry = None


class Store:
    """해시맵 조회 + O(1) LRU 이동 + O(log n) TTL 갱신·삭제."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._entries = HashMap()
        self._lru = DoublyLinkedList()
        self._expiry = MinHeap()
        self.used_memory = 0
        self.maxmemory = 0
        self.evicted_keys = 0

    def _delete(self, entry):
        """삭제 경로를 통합하여 세 자료구조와 메모리 합계를 동기화한다."""
        if entry.expiry is not None:
            self._expiry.remove(entry.expiry)
            entry.expiry = None
        self._lru.remove_node(entry.lru_node)
        self._entries.remove(entry.key)
        self.used_memory -= entry.byte_size

    def purge_expired(self):
        now = self._clock()
        while self._expiry.size and self._expiry.peek().priority <= now:
            self._delete(self._expiry.peek().value)

    def next_expiry_delay(self):
        """데몬이 입력을 기다리는 동안에도 TTL 시점에 깨어나게 한다."""
        if self._expiry.size == 0:
            return None
        return max(0.0, self._expiry.peek().priority - self._clock())

    def set(self, key, value):
        self.purge_expired()
        byte_size = len(key.encode("utf-8")) + len(value.encode("utf-8"))
        if self.maxmemory and byte_size > self.maxmemory:
            raise OutOfMemoryError()
        entry = self._entries.get(key)
        if entry is None:
            entry = Entry(key, value, byte_size)
            entry.lru_node = self._lru.insert_front(entry)
            self._entries.put(key, entry)
        else:
            if entry.expiry is not None:
                self._expiry.remove(entry.expiry)
                entry.expiry = None
            self.used_memory -= entry.byte_size
            entry.value, entry.byte_size = value, byte_size
            self._lru.move_to_front(entry.lru_node)
        self.used_memory += byte_size
        while self.maxmemory and self.used_memory > self.maxmemory:
            self._delete(self._lru.peek_back())
            self.evicted_keys += 1

    def get(self, key):
        self.purge_expired()
        entry = self._entries.get(key)
        if entry is None:
            return None
        self._lru.move_to_front(entry.lru_node)
        return entry.value

    def delete(self, key):
        self.purge_expired()
        entry = self._entries.get(key)
        if entry is None:
            return 0
        self._delete(entry)
        return 1

    def exists(self, key):
        self.purge_expired()
        return int(self._entries.contains(key))

    def dbsize(self):
        self.purge_expired()
        return self._entries.size

    def keys(self):
        self.purge_expired()
        return self._entries.keys()

    def configure_maxmemory(self, byte_limit):
        if byte_limit < 0:
            raise ValueError("maxmemory must not be negative")
        self.purge_expired()
        # 과제는 SET 이후의 제거만 명시하므로 설정 시 기존 키를 제거하지 않는다.
        self.maxmemory = byte_limit

    def memory_info(self):
        self.purge_expired()
        return self.used_memory, self.maxmemory, self.evicted_keys

    def expire(self, key, seconds):
        self.purge_expired()
        entry = self._entries.get(key)
        if entry is None:
            return 0
        if seconds <= 0:
            self._delete(entry)
            return 1
        deadline = self._clock() + seconds
        if entry.expiry is None:
            entry.expiry = self._expiry.push(deadline, entry)
        else:
            self._expiry.update(entry.expiry, deadline)
        return 1

    def ttl(self, key):
        self.purge_expired()
        entry = self._entries.get(key)
        if entry is None:
            return -2
        if entry.expiry is None:
            return -1
        return max(0, int(entry.expiry.priority - self._clock()))
