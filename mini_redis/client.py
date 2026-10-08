"""두 CLI가 공유하는 데몬 연결과 요청·응답 전송."""

import os
import socket
import subprocess
import sys
import time

from .commands import Result, quote
from .protocol import FrameDecoder, ProtocolError, encode_frame
from .responses import (ServerError, parse_boolean_reply, parse_integer_reply, parse_keys,
                        parse_memory_info, parse_message, parse_ok,
                        parse_subscription, parse_value)
from .runtime import RuntimePaths
from .structures.linked_list import DoublyLinkedList

__all__ = ("Client", "ServerError")


class Client:
    """요청을 순차 전송하고 응답 사이에 도착한 구독 이벤트를 분리한다."""

    def __init__(self, sock):
        self.socket = sock
        self.decoder = FrameDecoder()
        self._events = DoublyLinkedList()
        self._event_bytes = 0

    @classmethod
    def _connect(cls, paths):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            sock.connect(paths.socket)
        except OSError:
            sock.close()
            raise
        return cls(sock)

    @classmethod
    def open(cls, paths=None, auto_start=False):
        """기존 데몬에 연결한다. 명시적 시작 명령만 auto_start를 사용한다."""
        if paths is None:
            paths = RuntimePaths()
        if not auto_start:
            return cls._connect(paths)
        with paths.lock("startup"):
            # 종료 중인 데몬에 연결하지 않고, 다른 클라이언트의 시작도 기다린다.
            try:
                return cls._connect(paths)
            except (FileNotFoundError, ConnectionRefusedError):
                pass
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            subprocess.run(
                (sys.executable, "-m", "mini_redis.daemon", "--runtime-dir",
                 paths.directory, "--background"),
                cwd=project_root, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=True, timeout=5,
            )
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    return cls._connect(paths)
                except (FileNotFoundError, ConnectionRefusedError):
                    time.sleep(0.02)
            raise ConnectionError("daemon did not become ready within 3 seconds")

    def receive(self):
        while True:
            message = self.decoder.next_frame()
            if message is not None:
                return message
            data = self.socket.recv(65536)
            if not data:
                raise ConnectionError("daemon closed the connection")
            self.decoder.feed(data)

    def request(self, text="", kind="Q", on_event=None):
        """REPL용 원문 요청. 콜백이 없는 구독 이벤트는 후속 수신을 위해 보관한다."""
        self.socket.sendall(encode_frame(kind, text))
        while True:
            response_kind, body = self.receive()
            if response_kind == "M":
                if on_event is not None:
                    on_event(body)
                else:
                    self._buffer_event(body)
            elif response_kind in ("R", "E"):
                return Result(body, response_kind == "E")
            else:
                raise ProtocolError("unexpected daemon response")

    def _buffer_event(self, text):
        byte_size = len(text.encode("utf-8")) + 5
        if self._event_bytes + byte_size > 4 * 1024 * 1024:
            self.close()
            raise ProtocolError("client Pub/Sub buffer exceeds 4 MiB")
        self._events.insert_back(text)
        self._event_bytes += byte_size

    def execute(self, *arguments, on_event=None):
        """인자를 안전하게 인코딩한다. 명령 파싱·검증은 데몬에서 수행한다."""
        line = " ".join(quote(str(argument)) for argument in arguments)
        return self.request(line, on_event=on_event)

    def set(self, key, value):
        """저장 성공 시 True를 반환하며 데몬 오류는 ServerError로 전달한다."""
        return parse_ok(self.execute("SET", key, value))

    def get(self, key):
        """저장된 문자열을 반환하며 없거나 만료된 키는 None이다."""
        return parse_value(self.execute("GET", key))

    def delete(self, key):
        """삭제된 키 수를 정수로 반환한다."""
        return parse_integer_reply(self.execute("DEL", key))

    def exists(self, key):
        """키 존재 여부를 bool로 반환한다."""
        return parse_boolean_reply(self.execute("EXISTS", key))

    def dbsize(self):
        """만료된 키를 제외한 키 개수를 정수로 반환한다."""
        return parse_integer_reply(self.execute("DBSIZE"))

    def keys(self):
        """키 목록을 순서 보장 없는 tuple로 반환한다."""
        return parse_keys(self.execute("KEYS"))

    def configure_maxmemory(self, byte_limit):
        """메모리 제한 설정 성공 시 True를 반환한다. 0은 무제한이다."""
        return parse_ok(self.execute("CONFIG", "SET", "maxmemory", byte_limit))

    def memory_info(self):
        """used_memory, maxmemory, evicted_keys 필드를 가진 MemoryInfo를 반환한다."""
        return parse_memory_info(self.execute("INFO", "memory"))

    def expire(self, key, seconds):
        """만료 설정 성공 시 True, 키가 없으면 False를 반환한다."""
        return parse_boolean_reply(self.execute("EXPIRE", key, seconds))

    def ttl(self, key):
        """남은 초를 반환한다. 키 없음은 -2, 만료 설정 없음은 -1이다."""
        return parse_integer_reply(self.execute("TTL", key))

    def publish(self, channel, message):
        """메시지를 큐에 넣은 구독자 수를 정수로 반환한다."""
        return parse_integer_reply(self.execute("PUBLISH", channel, message))

    def subscribe(self, channel):
        """구독 후 이 연결이 구독하는 채널 수를 반환한다."""
        return parse_subscription(self.execute("SUBSCRIBE", channel), str(channel))

    def receive_message(self, timeout=None):
        """서버가 push한 Message를 수신한다. 기본값은 시간 제한 없이 대기한다."""
        if self._events.size:
            text = self._events.remove_front()
            self._event_bytes -= len(text.encode("utf-8")) + 5
            return parse_message(text)
        previous_timeout = self.socket.gettimeout()
        try:
            self.socket.settimeout(timeout)
            kind, text = self.receive()
        finally:
            self.socket.settimeout(previous_timeout)
        if kind != "M":
            raise ProtocolError("expected Pub/Sub event")
        return parse_message(text)

    def listen(self):
        """구독 메시지를 계속 생성한다. 연결 종료와 오류는 호출자에게 전달한다."""
        while True:
            yield self.receive_message()

    def close(self):
        self.socket.close()
        self._events = DoublyLinkedList()
        self._event_bytes = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
