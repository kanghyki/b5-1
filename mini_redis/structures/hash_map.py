"""UTF-8 문자열 키, 직접 계산한 해시, 연결 리스트 체이닝을 사용하는 맵."""

from .linked_list import DoublyLinkedList


class Pair:
    def __init__(self, key, value):
        self.key, self.value = key, value


class HashMap:
    """평균 O(1) 조회. 부하율 0.75 초과 시 버킷을 두 배 확장한다."""

    def __init__(self, capacity=8):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._buckets = [None] * capacity
        self.size = 0

    @property
    def capacity(self):
        return len(self._buckets)

    @staticmethod
    def hash_key(key):
        """각 UTF-8 바이트를 XOR하고 곱하는 64비트 FNV-1a 해시."""
        result = 14695981039346656037
        for byte in key.encode("utf-8"):
            result = ((result ^ byte) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
        return result

    def _find(self, key):
        bucket = self._buckets[self.hash_key(key) % self.capacity]
        if bucket is not None:
            for node in bucket.nodes():
                if node.data.key == key:
                    return bucket, node
        return bucket, None

    def get(self, key, default=None):
        _, node = self._find(key)
        return default if node is None else node.data.value

    def contains(self, key):
        return self._find(key)[1] is not None

    def _insert(self, pair):
        index = self.hash_key(pair.key) % self.capacity
        if self._buckets[index] is None:
            self._buckets[index] = DoublyLinkedList()
        self._buckets[index].insert_back(pair)

    def put(self, key, value):
        _, node = self._find(key)
        if node is not None:
            node.data.value = value
            return
        self._insert(Pair(key, value))
        self.size += 1
        if self.size * 4 > self.capacity * 3:
            old_buckets = self._buckets
            self._buckets = [None] * (self.capacity * 2)
            for bucket in old_buckets:
                if bucket is not None:
                    for pair in bucket:
                        self._insert(pair)

    def remove(self, key):
        bucket, node = self._find(key)
        if node is None:
            return False
        bucket.remove_node(node)
        self.size -= 1
        return True

    def keys(self):
        for pair in self.items():
            yield pair.key

    def items(self):
        for bucket in self._buckets:
            if bucket is not None:
                for pair in bucket:
                    yield pair
