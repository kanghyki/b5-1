"""연결별 구독 상태와 제한된 FIFO 출력 큐를 관리하는 채널 브로커."""

from .commands import quote
from .protocol import MAX_FRAME
from .structures.hash_map import HashMap
from .structures.linked_list import DoublyLinkedList


class Delivery:
    def __init__(self, kind, text):
        self.kind, self.text = kind, text
        # 길이 접두사 4바이트와 종류 표시 1바이트를 포함한다.
        self.byte_size = 5 + len(text.encode("utf-8"))


class Subscriber:
    """메시지와 일반 응답에 동일한 FIFO를 사용하여 전송 순서를 보존한다."""

    def __init__(self, identifier, max_pending_bytes=4 * 1024 * 1024):
        self.identifier = identifier
        self.channels = HashMap()
        self.pending = DoublyLinkedList()
        self.pending_bytes = 0
        self.max_pending_bytes = max_pending_bytes
        self.overflowed = False

    def enqueue(self, kind, text):
        if self.overflowed:
            return False
        delivery = Delivery(kind, text)
        if (delivery.byte_size - 4 > MAX_FRAME or
                self.pending_bytes + delivery.byte_size > self.max_pending_bytes):
            self.overflowed = True
            return False
        self.pending.insert_back(delivery)
        self.pending_bytes += delivery.byte_size
        return True

    def pop(self):
        delivery = self.pending.remove_front()
        self.pending_bytes -= delivery.byte_size
        return delivery


class PubSub:
    """직접 구현한 해시맵으로 채널과 구독자를 조회한다."""

    def __init__(self):
        self.channels = HashMap()

    def subscribe(self, channel, subscriber):
        members = self.channels.get(channel)
        if members is None:
            members = HashMap()
            self.channels.put(channel, members)
        members.put(subscriber.identifier, subscriber)
        subscriber.channels.put(channel, True)
        return subscriber.channels.size

    def publish(self, channel, message):
        members = self.channels.get(channel)
        if members is None:
            return 0
        text = "message %s %s" % (quote(channel), quote(message))
        delivered = 0
        for pair in members.items():
            if pair.value.enqueue("M", text):
                delivered += 1
        return delivered

    def disconnect(self, subscriber):
        """마지막 구독자가 나간 채널도 제거하여 연결 상태를 남기지 않는다."""
        for channel in subscriber.channels.keys():
            members = self.channels.get(channel)
            members.remove(subscriber.identifier)
            if members.size == 0:
                self.channels.remove(channel)
        subscriber.channels = HashMap()
        subscriber.pending = DoublyLinkedList()
        subscriber.pending_bytes = 0
