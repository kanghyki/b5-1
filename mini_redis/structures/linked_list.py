"""노드 참조로 삽입·삭제·이동을 O(1)에 수행하는 이중 연결 리스트."""


class Node:
    def __init__(self, data):
        self.data = data
        self.prev = None
        self.next = None


class DoublyLinkedList:
    """두 센티널 사이에 데이터를 저장하며 LRU와 해시맵 버킷에 재사용한다."""

    def __init__(self):
        self._head = Node(None)
        self._tail = Node(None)
        self._head.next = self._tail
        self._tail.prev = self._head
        self.size = 0

    def _insert_between(self, node, left, right):
        node.prev, node.next = left, right
        left.next, right.prev = node, node
        self.size += 1
        return node

    def insert_front(self, data):
        return self._insert_between(Node(data), self._head, self._head.next)

    def insert_back(self, data):
        return self._insert_between(Node(data), self._tail.prev, self._tail)

    def remove_node(self, node):
        """이 리스트에 연결된 데이터 노드만 넘겨야 한다."""
        node.prev.next, node.next.prev = node.next, node.prev
        node.prev = node.next = None
        self.size -= 1
        return node.data

    def remove_front(self):
        if self.size == 0:
            raise IndexError("empty list")
        return self.remove_node(self._head.next)

    def remove_back(self):
        if self.size == 0:
            raise IndexError("empty list")
        return self.remove_node(self._tail.prev)

    def move_to_front(self, node):
        self.remove_node(node)
        self._insert_between(node, self._head, self._head.next)

    def peek_front(self):
        if self.size == 0:
            raise IndexError("empty list")
        return self._head.next.data

    def peek_back(self):
        if self.size == 0:
            raise IndexError("empty list")
        return self._tail.prev.data

    def nodes(self):
        """순회 중 현재 노드 삭제가 가능하도록 다음 참조를 먼저 보관한다."""
        node = self._head.next
        while node is not self._tail:
            following = node.next
            yield node
            node = following

    def __iter__(self):
        for node in self.nodes():
            yield node.data
