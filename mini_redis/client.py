"""두 CLI가 공유하는 데몬 자동 시작과 요청·응답 전송."""

import os
import socket
import subprocess
import sys
import time

from .commands import Result
from .protocol import FrameDecoder, ProtocolError, encode_frame


class Client:
    """요청을 순차 전송하고 응답 사이에 도착한 구독 이벤트를 분리한다."""

    def __init__(self, sock):
        self.socket = sock
        self.decoder = FrameDecoder()

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
    def open(cls, paths, auto_start=True):
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
        self.socket.sendall(encode_frame(kind, text))
        while True:
            response_kind, body = self.receive()
            if response_kind == "M":
                if on_event is not None:
                    on_event(body)
            elif response_kind in ("R", "E"):
                return Result(body, response_kind == "E")
            else:
                raise ProtocolError("unexpected daemon response")

    def close(self):
        self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
