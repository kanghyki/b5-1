"""단일 이벤트 루프에서 로컬 클라이언트, TTL, Pub/Sub을 처리하는 데몬."""

import os
import select
import signal
import socket
import stat
import sys
import time

from .commands import CommandProcessor
from .protocol import FrameDecoder, MAX_FRAME, ProtocolError, encode_frame
from .pubsub import PubSub, Subscriber
from .runtime import RuntimePaths
from .structures.hash_map import HashMap


class Connection:
    def __init__(self, sock, identifier):
        self.sock = sock
        self.key = str(sock.fileno())
        self.subscriber = Subscriber(identifier)
        self.decoder = FrameDecoder()
        self.outgoing = None
        self.offset = 0


class Daemon:
    """각 연결은 독립 버퍼를 가지고 모든 저장소 명령은 순차 실행한다."""

    def __init__(self, paths):
        self.paths = paths
        self.broker = PubSub()
        self.commands = CommandProcessor(pubsub=self.broker)
        self.connections = HashMap()
        self.listener = None
        self.running = True
        self.stopping = False
        self.stop_deadline = None
        self._sequence = 0
        self._owns_socket = False

    def _bind(self):
        if os.path.lexists(self.paths.socket):
            details = os.lstat(self.paths.socket)
            if not stat.S_ISSOCK(details.st_mode) or details.st_uid != os.getuid():
                raise ValueError("refusing to replace a non-socket runtime file")
            os.unlink(self.paths.socket)
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.paths.socket)
        self._owns_socket = True
        os.chmod(self.paths.socket, 0o600)
        self.listener.listen(64)
        self.listener.setblocking(False)

    def _signal_stop(self, signum, frame):
        self.running = False

    def _close_connection(self, connection):
        self.broker.disconnect(connection.subscriber)
        self.connections.remove(connection.key)
        connection.sock.close()

    def _accept(self):
        # 한 번에 받는 연결 수를 제한하여 기존 클라이언트도 처리한다.
        for _ in range(64):
            try:
                sock, _ = self.listener.accept()
            except BlockingIOError:
                return
            sock.setblocking(False)
            self._sequence += 1
            connection = Connection(sock, str(self._sequence))
            self.connections.put(connection.key, connection)

    @staticmethod
    def _respond(connection, text, error=False):
        if 1 + len(text.encode("utf-8")) > MAX_FRAME:
            text, error = "(error) ERR response exceeds IPC message limit", True
        connection.subscriber.enqueue("E" if error else "R", text)

    def _request(self, connection, kind, text):
        if kind == "Q":
            result = self.commands.execute(text, connection.subscriber)
            self._respond(connection, result.text, result.error)
        elif kind == "P" and text == "":
            self._respond(connection, "PONG")
        elif kind == "S" and text == "":
            self._respond(connection, "OK")
            self.stopping = True
            self.stop_deadline = time.monotonic() + 2.0
        else:
            raise ProtocolError("expected a client request")

    def _read(self, connection):
        try:
            data = connection.sock.recv(65536)
            if not data:
                self._close_connection(connection)
                return
            connection.decoder.feed(data)
            while not self.stopping and not connection.subscriber.overflowed:
                message = connection.decoder.next_frame()
                if message is None:
                    break
                self._request(connection, *message)
        except (OSError, ProtocolError):
            self._close_connection(connection)

    def _write(self, connection):
        subscriber = connection.subscriber
        budget = 65536
        try:
            while subscriber.pending.size and budget > 0:
                if connection.outgoing is None:
                    delivery = subscriber.pending.peek_front()
                    connection.outgoing = encode_frame(delivery.kind, delivery.text)
                    connection.offset = 0
                view = memoryview(connection.outgoing)
                sent = connection.sock.send(view[connection.offset:connection.offset + budget])
                if sent == 0:
                    self._close_connection(connection)
                    return
                connection.offset += sent
                budget -= sent
                if connection.offset == len(connection.outgoing):
                    subscriber.pop()
                    connection.outgoing = None
        except BlockingIOError:
            return
        except (OSError, ProtocolError):
            self._close_connection(connection)

    def _loop(self):
        while self.running:
            self.commands.store.purge_expired()
            for pair in self.connections.items():
                if pair.value.subscriber.overflowed:
                    self._close_connection(pair.value)
            writers = tuple(pair.value.sock for pair in self.connections.items()
                            if pair.value.subscriber.pending.size)
            if self.stopping and (not writers or time.monotonic() >= self.stop_deadline):
                break
            readers = () if self.stopping else (self.listener,) + tuple(
                pair.value.sock for pair in self.connections.items()
            )
            timeout = 1.0
            delay = self.commands.store.next_expiry_delay()
            if delay is not None:
                timeout = min(timeout, delay)
            if self.stopping:
                timeout = min(timeout, max(0, self.stop_deadline - time.monotonic()))
            try:
                readable, writable, _ = select.select(readers, writers, (), timeout)
            except InterruptedError:
                continue
            for sock in readable:
                if self.stopping:
                    break
                if sock is self.listener:
                    self._accept()
                else:
                    connection = self.connections.get(str(sock.fileno()))
                    if connection is not None:
                        self._read(connection)
            for sock in writable:
                connection = self.connections.get(str(sock.fileno()))
                if connection is not None and not connection.subscriber.overflowed:
                    self._write(connection)

    def serve(self):
        """실행 동안 잠금을 보유하고 종료 시 소켓과 연결을 정리한다."""
        with self.paths.lock("daemon", blocking=False):
            previous_term = signal.signal(signal.SIGTERM, self._signal_stop)
            previous_int = signal.signal(signal.SIGINT, self._signal_stop)
            try:
                self._bind()
                self._loop()
            finally:
                for pair in self.connections.items():
                    self._close_connection(pair.value)
                if self.listener is not None:
                    self.listener.close()
                if self._owns_socket:
                    os.unlink(self.paths.socket)
                signal.signal(signal.SIGTERM, previous_term)
                signal.signal(signal.SIGINT, previous_int)


def main(argv=None):
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    background = False
    if arguments and arguments[-1] == "--background":
        background = True
        arguments = arguments[:-1]
    if len(arguments) != 2 or arguments[0] != "--runtime-dir":
        print("usage: python -m mini_redis.daemon --runtime-dir PATH [--background]",
              file=sys.stderr)
        return 2
    try:
        paths = RuntimePaths(arguments[1])
        if background:
            if os.fork() > 0:
                return 0
            os.setsid()
        Daemon(paths).serve()
        return 0
    except (OSError, ValueError) as error:
        print("daemon: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
