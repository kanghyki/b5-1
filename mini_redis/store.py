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
        self.expire_at = None


class Store:
    """해시맵 조회 + O(1) LRU 이동 + 힙 기반 lazy TTL 만료."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._entries = HashMap()
        self._lru = DoublyLinkedList()
        self._expiry = MinHeap()
        self.used_memory = 0
        self.maxmemory = 0
        self.evicted_keys = 0

    def _delete(self, entry):
        """삭제 경로를 통합한다. 힙의 항목은 purge_expired()가 꺼낼 때 버린다."""
        self._lru.remove_node(entry.lru_node)
        self._entries.remove(entry.key)
        self.used_memory -= entry.byte_size

    def purge_expired(self):
        """만료 시각이 지난 힙 항목을 꺼내 현재 TTL과 일치할 때만 키를 삭제한다."""
        now = self._clock()
        while self._expiry.size and self._expiry.peek()[0] <= now:
            expire_at, key = self._expiry.pop()
            entry = self._entries.get(key)
            # TTL이 바뀌었거나 키가 삭제·재생성되었다면 낡은 항목이다.
            if entry is not None and entry.expire_at == expire_at:
                self._delete(entry)

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
            entry.expire_at = None
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
        entry.expire_at = self._clock() + seconds
        self._expiry.push((entry.expire_at, key))
        return 1

    def ttl(self, key):
        self.purge_expired()
        entry = self._entries.get(key)
        if entry is None:
            return -2
        if entry.expire_at is None:
            return -1
        return max(0, int(entry.expire_at - self._clock()))
