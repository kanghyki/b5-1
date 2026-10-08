import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from mini_redis.client import Client, ServerError
from mini_redis.commands import quote
from mini_redis.protocol import MAX_FRAME, ProtocolError, encode_frame
from mini_redis.runtime import RuntimePaths
from mini_redis.structures.linked_list import DoublyLinkedList


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ScriptedSocket:
    """응답 바이트를 나누어 전달해 비정상 응답과 이벤트 순서를 재현한다."""

    def __init__(self, wire):
        self.wire, self.offset = wire, 0
        self.timeout = 5
        self.closed = False

    def sendall(self, data):
        pass

    def recv(self, size):
        data = self.wire[self.offset:self.offset + size]
        self.offset += len(data)
        return data

    def gettimeout(self):
        return self.timeout

    def settimeout(self, timeout):
        self.timeout = timeout

    def close(self):
        self.closed = True


class ClientResponseTests(unittest.TestCase):
    def test_malformed_typed_replies_raise_protocol_errors(self):
        cases = (
            ("set", ("key", "value"), "STORED"),
            ("get", ("key",), '"value" junk'),
            ("get", ("key",), "unquoted"),
            ("exists", ("key",), "(integer) 2"),
            ("publish", ("news", "hi"), "(integer) not-a-number"),
            ("publish", ("news", "hi"), "(integer) 9223372036854775808"),
            ("keys", (), '2. "key"'),
            ("memory_info", (), "used_memory:1\nmaxmemory:0"),
            ("memory_info", (), "used_memory:-1\nmaxmemory:0\nevicted_keys:0"),
            ("subscribe", ("news",), 'subscribe "other" (integer) 1'),
        )
        for method, arguments, reply in cases:
            with self.subTest(method=method, reply=reply):
                client = Client(ScriptedSocket(encode_frame("R", reply)))
                with self.assertRaises(ProtocolError):
                    getattr(client, method)(*arguments)

    def test_server_errors_become_exceptions_but_raw_request_preserves_result(self):
        text = "(error) ERR value is not an integer or out of range"
        client = Client(ScriptedSocket(encode_frame("E", text)))
        with self.assertRaises(ServerError) as caught:
            client.configure_maxmemory("bad")
        self.assertEqual(str(caught.exception), text)
        raw = Client(ScriptedSocket(encode_frame("E", text))).request("CONFIG SET maxmemory bad")
        self.assertTrue(raw.error)
        self.assertEqual(raw.text, text)

    def test_events_during_response_wait_are_kept_in_fifo_order(self):
        wire = (encode_frame("M", 'message "news" "first"') +
                encode_frame("M", 'message "other" "second"') +
                encode_frame("R", '"value"'))
        client = Client(ScriptedSocket(wire))
        self.assertEqual(client.get("key"), "value")
        first = client.receive_message()
        second = next(client.listen())
        self.assertEqual((first.channel, first.data), ("news", "first"))
        self.assertEqual((second.channel, second.data), ("other", "second"))
        self.assertEqual(client._event_bytes, 0)

    def test_raw_request_callback_receives_events_without_duplicating_them(self):
        text = 'message "news" "hello"'
        events = DoublyLinkedList()
        client = Client(ScriptedSocket(encode_frame("M", text) + encode_frame("R", "OK")))
        result = client.request("SET key value", on_event=events.insert_back)
        self.assertEqual(result.text, "OK")
        self.assertEqual(tuple(events), (text,))
        self.assertEqual(client._events.size, 0)

    def test_message_wait_disables_timeout_and_restores_it_after_error(self):
        class CheckingSocket(ScriptedSocket):
            def recv(self, size):
                if self.timeout is not None:
                    raise AssertionError("message wait must not use the request timeout")
                return super().recv(size)

        sock = CheckingSocket(encode_frame("M", 'message "news" "hello"'))
        client = Client(sock)
        self.assertEqual(client.receive_message().data, "hello")
        self.assertEqual(sock.gettimeout(), 5)
        with self.assertRaises(ConnectionError):
            client.receive_message()
        self.assertEqual(sock.gettimeout(), 5)

    def test_invalid_messages_raise_protocol_error_and_restore_timeout(self):
        for kind, text in (("R", "OK"), ("M", "message only-channel"),
                           ("M", 'message "news" "unterminated')):
            sock = ScriptedSocket(encode_frame(kind, text))
            with self.assertRaises(ProtocolError):
                Client(sock).receive_message()
            self.assertEqual(sock.gettimeout(), 5)

    def test_event_buffer_overflow_closes_connection_without_stale_reply(self):
        prefix = 'message "news" "'
        text = prefix + "x" * (MAX_FRAME - len(prefix) - 2) + '"'
        wire = encode_frame("M", text) * 4 + encode_frame("R", "(integer) 0")
        sock = ScriptedSocket(wire)
        client = Client(sock)
        with self.assertRaisesRegex(ProtocolError, "buffer exceeds"):
            client.dbsize()
        self.assertTrue(sock.closed)
        self.assertEqual(client._event_bytes, 0)


class ClientIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="mr-api-")
        self.addCleanup(self.directory.cleanup)
        self.paths = RuntimePaths(self.directory.name)
        self.addCleanup(self.stop_daemon)
        self.client = self.other_client()

    def other_client(self):
        client = Client.open(self.paths)
        self.addCleanup(client.close)
        return client

    def stop_daemon(self):
        try:
            with Client.open(self.paths, auto_start=False) as client:
                self.assertFalse(client.request(kind="S").error)
        except (FileNotFoundError, ConnectionRefusedError):
            return
        deadline = time.monotonic() + 3
        while os.path.exists(self.paths.socket) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(os.path.exists(self.paths.socket))

    def test_storage_methods_return_python_values_and_preserve_escaped_strings(self):
        client = self.client
        key, value = '키 "공백"\n', '값 \\ "따옴표"\n\t'
        self.assertIsNone(client.get(key))
        self.assertEqual(client.keys(), ())
        self.assertIs(client.set(key, value), True)
        self.assertEqual(client.get(key), value)
        self.assertIs(client.exists(key), True)
        self.assertIs(client.exists("missing"), False)
        self.assertEqual(client.keys(), (key,))
        self.assertIsInstance(client.dbsize(), int)
        self.assertEqual(client.dbsize(), 1)
        self.assertIs(client.set("empty", ""), True)
        self.assertEqual(client.get("empty"), "")
        self.assertEqual(client.delete("empty"), 1)
        self.assertEqual(client.delete("empty"), 0)
        info = client.memory_info()
        self.assertEqual(info.used_memory, len(key.encode()) + len(value.encode()))
        self.assertEqual((info.maxmemory, info.evicted_keys), (0, 0))
        self.assertEqual(client.delete(key), 1)
        self.assertEqual(client.memory_info().used_memory, 0)

    def test_limits_ttl_and_server_errors_remain_owned_by_daemon(self):
        client = self.client
        self.assertIs(client.configure_maxmemory(4), True)
        client.set("a", "1")
        client.set("b", "2")
        client.get("a")
        client.set("c", "3")
        self.assertIsNone(client.get("b"))
        self.assertEqual(client.memory_info().evicted_keys, 1)
        with self.assertRaisesRegex(ServerError, "OOM"):
            client.set("a", "too large")
        self.assertEqual(client.get("a"), "1")
        with self.assertRaisesRegex(ServerError, "integer or out of range"):
            client.configure_maxmemory(-1)
        with self.assertRaisesRegex(ServerError, "integer or out of range"):
            client.expire("a", "not-an-integer")
        self.assertEqual(client.ttl("a"), -1)
        self.assertEqual(client.ttl("missing"), -2)
        self.assertIs(client.expire("missing", 1), False)
        self.assertIs(client.expire("a", 60), True)
        self.assertGreaterEqual(client.ttl("a"), 59)
        client.set("a", "x")
        self.assertEqual(client.ttl("a"), -1)
        self.assertIs(client.expire("a", 0), True)
        self.assertIsNone(client.get("a"))

    def test_pubsub_is_shared_between_clients_and_structured_messages_roundtrip(self):
        channel, data = 'news "한 글"\n', 'hello "world" \\ \n\t'
        publisher = self.other_client()
        self.assertEqual(publisher.publish(channel, "before"), 0)
        self.assertEqual(self.client.subscribe(channel), 1)
        self.assertEqual(self.client.subscribe(channel), 1)
        self.assertEqual(self.client.subscribe("other"), 2)
        self.assertEqual(publisher.publish(channel, data), 1)
        message = self.client.receive_message(timeout=2)
        self.assertEqual((message.channel, message.data), (channel, data))
        self.assertEqual(message.text, "message %s %s" % (quote(channel), quote(data)))
        self.assertEqual(publisher.dbsize(), 0)

    def test_self_publish_and_other_requests_do_not_drop_events(self):
        client = self.client
        self.assertEqual(client.subscribe("news"), 1)
        self.assertEqual(client.publish("news", "first"), 1)
        self.assertTrue(client.set("key", "value"))
        self.assertEqual(client.publish("news", "second"), 1)
        self.assertEqual(client.get("key"), "value")
        self.assertEqual(client.receive_message(timeout=1).data, "first")
        self.assertEqual(client.receive_message(timeout=1).data, "second")
        self.assertEqual(client._event_bytes, 0)

    def test_receive_timeout_does_not_change_later_requests_or_subscription(self):
        self.client.subscribe("news")
        original_timeout = self.client.socket.gettimeout()
        with self.assertRaises(socket.timeout):
            self.client.receive_message(timeout=0.02)
        self.assertEqual(self.client.socket.gettimeout(), original_timeout)
        publisher = self.other_client()
        self.assertEqual(publisher.publish("news", "after"), 1)
        self.assertEqual(self.client.receive_message(timeout=1).data, "after")
        self.assertEqual(self.client.dbsize(), 0)

    def test_listen_waits_for_push_from_another_process(self):
        self.client.subscribe("news")
        self.client.socket.settimeout(0.01)
        code = (
            "import time\n"
            "from mini_redis.client import Client\n"
            "from mini_redis.runtime import RuntimePaths\n"
            "time.sleep(0.1)\n"
            "with Client.open(RuntimePaths(%r)) as client:\n"
            "    client.publish('news', 'delayed')\n" % self.paths.directory
        )
        process = subprocess.Popen((sys.executable, "-c", code), cwd=ROOT,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        def deadline_expired(signum, frame):
            raise AssertionError("publisher did not deliver a message within 3 seconds")

        previous_handler = signal.signal(signal.SIGALRM, deadline_expired)
        signal.alarm(3)
        try:
            message = next(self.client.listen())
            self.assertEqual((message.channel, message.data), ("news", "delayed"))
            self.assertEqual(self.client.socket.gettimeout(), 0.01)
            process.wait(timeout=3)
            self.assertEqual(process.returncode, 0, process.stderr.read().decode())
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_handler)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
