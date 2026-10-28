"""(expire_at, key) 같은 비교 가능한 요소를 다루는 배열 기반 최소 힙."""


class MinHeap:
    """가장 작은 요소를 O(1)에 조회하고 O(log n)에 추가·제거한다."""

    def __init__(self):
        self._array = [None] * 8
        self.size = 0

    def _swap(self, first, second):
        self._array[first], self._array[second] = self._array[second], self._array[first]

    def _heapify_up(self, index):
        while index > 0:
            parent = (index - 1) // 2
            if self._array[parent] <= self._array[index]:
                break
            self._swap(parent, index)
            index = parent

    def _heapify_down(self, index):
        while 2 * index + 1 < self.size:
            child = 2 * index + 1
            if child + 1 < self.size and self._array[child + 1] < self._array[child]:
                child += 1
            if self._array[index] <= self._array[child]:
                break
            self._swap(index, child)
            index = child

    def push(self, item):
        if self.size == len(self._array):
            expanded = [None] * (self.size * 2)
            for index in range(self.size):
                expanded[index] = self._array[index]
            self._array = expanded
        self._array[self.size] = item
        self.size += 1
        self._heapify_up(self.size - 1)

    def peek(self):
        if self.size == 0:
            raise IndexError("empty heap")
        return self._array[0]

    def pop(self):
        item = self.peek()
        self.size -= 1
        self._array[0] = self._array[self.size]
        self._array[self.size] = None
        self._heapify_down(0)
        return item
