"""인덱스 참조를 이용해 임의 항목의 갱신·삭제도 지원하는 최소 힙."""


class HeapItem:
    def __init__(self, priority, value, index):
        self.priority, self.value, self.index = priority, value, index


class MinHeap:
    """TTL 변경과 삭제 시 오래된 힙 항목을 남기지 않는 인덱스 힙."""

    def __init__(self):
        self._array = [None] * 8
        self.size = 0

    def _swap(self, first, second):
        self._array[first], self._array[second] = self._array[second], self._array[first]
        self._array[first].index = first
        self._array[second].index = second

    def _heapify_up(self, index):
        while index > 0:
            parent = (index - 1) // 2
            if self._array[parent].priority <= self._array[index].priority:
                break
            self._swap(parent, index)
            index = parent

    def _heapify_down(self, index):
        while 2 * index + 1 < self.size:
            child = 2 * index + 1
            if child + 1 < self.size:
                if self._array[child + 1].priority < self._array[child].priority:
                    child += 1
            if self._array[index].priority <= self._array[child].priority:
                break
            self._swap(index, child)
            index = child

    def push(self, priority, value):
        if self.size == len(self._array):
            expanded = [None] * (self.size * 2)
            for index in range(self.size):
                expanded[index] = self._array[index]
            self._array = expanded
        item = HeapItem(priority, value, self.size)
        self._array[self.size] = item
        self.size += 1
        self._heapify_up(item.index)
        return item

    def peek(self):
        if self.size == 0:
            raise IndexError("empty heap")
        return self._array[0]

    def pop(self):
        return self.remove(self.peek())

    def _validate(self, item):
        if not 0 <= item.index < self.size or self._array[item.index] is not item:
            raise ValueError("item does not belong to this heap")

    def remove(self, item):
        self._validate(item)
        index = item.index
        self.size -= 1
        if index != self.size:
            self._array[index] = self._array[self.size]
            self._array[index].index = index
        self._array[self.size] = None
        item.index = -1
        if index < self.size:
            replacement = self._array[index]
            self._heapify_up(index)
            self._heapify_down(replacement.index)
        return item

    def update(self, item, priority):
        self._validate(item)
        item.priority = priority
        self._heapify_up(item.index)
        self._heapify_down(item.index)
