import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from mini_redis.protocol import FrameDecoder, encode_frame


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Peer:
    def __init__(self, path):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(3)
        try:
            self.socket.connect(path)
        except OSError:
            self.socket.close()
            raise
        self.decoder = FrameDecoder()

    def send(self, text, kind="Q"):
        self.socket.sendall(encode_frame(kind, text))

    def receive(self):
        while True:
            message = self.decoder.next_frame()
            if message is not None:
                return message
            data = self.socket.recv(65536)
            if not data:
                raise EOFError("daemon disconnected")
            self.decoder.feed(data)

    def request(self, text):
        self.send(text)
        return self.receive()

    def close(self):
        self.socket.close()


class DaemonTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="mr-test-")
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "redis.sock")
        self.process = subprocess.Popen(
            (sys.executable, "-m", "mini_redis.daemon", "--runtime-dir", self.directory.name),
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        self.addCleanup(self.stop_process)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.fail(self.process.stderr.read().decode())
            try:
                peer = Peer(self.path)
                self.addCleanup(peer.close)
                self.peer = peer
                return
            except (FileNotFoundError, ConnectionRefusedError):
                time.sleep(0.01)
        self.fail("daemon did not become ready")

    def stop_process(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.process.stderr.close()

    def other_peer(self):
        peer = Peer(self.path)
        self.addCleanup(peer.close)
        return peer

    def test_shared_store_pubsub_and_disconnect_cleanup(self):
        other = self.other_peer()
        self.assertEqual(self.peer.request('SET key "hello world"'), ("R", "OK"))
        self.assertEqual(other.request("GET key"), ("R", '"hello world"'))
        self.assertEqual(self.peer.request("SUBSCRIBE news"),
                         ("R", 'subscribe "news" (integer) 1'))
        self.assertEqual(other.request("PUBLISH news hello"), ("R", "(integer) 1"))
        self.assertEqual(self.peer.receive(), ("M", 'message "news" "hello"'))
        self.peer.close()
        # 연결 종료는 비동기로 감지하므로 정리된 구독자 수를 기다린다.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if other.request("PUBLISH news after") == ("R", "(integer) 0"):
                break
            time.sleep(0.01)
        else:
            self.fail("subscription was not removed")

    def test_fragmented_frames_and_invalid_peer_do_not_break_daemon(self):
        wire = encode_frame("Q", "SET split value") + encode_frame("Q", "GET split")
        for byte in wire:
            self.peer.socket.sendall(bytes((byte,)))
        self.assertEqual(self.peer.receive(), ("R", "OK"))
        self.assertEqual(self.peer.receive(), ("R", '"value"'))
        invalid = self.other_peer()
        invalid.socket.sendall(bytes(4))
        self.assertEqual(invalid.socket.recv(1), b"")
        self.assertEqual(self.peer.request("DBSIZE"), ("R", "(integer) 1"))

    def test_large_response_and_multiple_messages_preserve_bytes(self):
        value = "가" * 40000
        self.assertEqual(self.peer.request('SET big "' + value + '"'), ("R", "OK"))
        self.assertEqual(self.peer.request("GET big"), ("R", '"' + value + '"'))
        self.peer.send("DBSIZE")
        self.peer.send("EXISTS big")
        self.assertEqual(self.peer.receive(), ("R", "(integer) 1"))
        self.assertEqual(self.peer.receive(), ("R", "(integer) 1"))

    def test_idle_expiration_shutdown_and_socket_removal(self):
        self.peer.request("SET key value")
        self.peer.request("EXPIRE key 1")
        time.sleep(1.05)
        self.assertEqual(self.peer.request("DBSIZE"), ("R", "(integer) 0"))
        self.peer.send("", "S")
        self.assertEqual(self.peer.receive(), ("R", "OK"))
        self.process.wait(timeout=3)
        self.assertEqual(self.process.returncode, 0)
        self.assertFalse(os.path.exists(self.path))

    def test_second_daemon_cannot_replace_running_socket(self):
        second = subprocess.run(
            (sys.executable, "-m", "mini_redis.daemon", "--runtime-dir", self.directory.name),
            cwd=ROOT, capture_output=True, timeout=3,
        )
        self.assertEqual(second.returncode, 1)
        self.assertEqual(self.peer.request("DBSIZE"), ("R", "(integer) 0"))


if __name__ == "__main__":
    unittest.main()
