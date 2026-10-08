import os
import pty
import select
import signal
import subprocess
import sys
import tempfile
import termios
import time
import unittest

from mini_redis.runtime import RuntimePaths


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_until(descriptor, needle, timeout=5):
    buffer = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select((descriptor,), (), (), max(0, deadline - time.monotonic()))
        if ready:
            data = os.read(descriptor, 65536)
            if not data:
                break
            buffer.extend(data)
            if needle in buffer:
                return bytes(buffer)
    raise AssertionError("did not receive %r; received %r" % (needle, bytes(buffer)))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="mr-cli-")
        self.addCleanup(self.directory.cleanup)
        self.command = (sys.executable, os.path.join(ROOT, "main.py"),
                        "--runtime-dir", self.directory.name)
        self.addCleanup(self.stop_daemon)

    def stop_daemon(self):
        subprocess.run(self.command + ("daemon", "stop"), cwd=ROOT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)

    def run_cli(self, *arguments, input=None):
        return subprocess.run(self.command + arguments, cwd=ROOT, input=input,
                              capture_output=True, text=True, timeout=6)

    def start_client(self, *arguments, **options):
        process = subprocess.Popen(self.command + arguments, cwd=ROOT, **options)
        self.addCleanup(self.stop_client, process)
        return process

    @staticmethod
    def stop_client(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    def test_automatic_start_shared_data_errors_and_restart(self):
        self.assertEqual(self.run_cli("daemon", "status").stdout, "stopped\n")
        self.assertEqual(self.run_cli("SET", "키", '한 글"\\\n').stdout, "OK\n")
        self.assertEqual(self.run_cli("GET", "키").stdout, '"한 글\\"\\\\\\n"\n')
        self.assertEqual(self.run_cli("daemon", "status").stdout, "running\n")
        error = self.run_cli("GET")
        self.assertEqual(error.returncode, 1)
        self.assertIn("wrong number of arguments", error.stdout)
        self.assertEqual(self.run_cli("daemon", "stop").stdout, "OK\n")
        self.assertFalse(os.path.exists(os.path.join(self.directory.name, "redis.sock")))
        self.assertEqual(self.run_cli("GET", "키").stdout, "(nil)\n")

    def test_concurrent_clients_start_only_one_shared_daemon(self):
        processes = tuple(self.start_client("SET", "key%d" % index, str(index),
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                          for index in range(8))
        for process in processes:
            stdout, stderr = process.communicate(timeout=6)
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertEqual(stdout, b"OK\n")
        self.assertEqual(self.run_cli("DBSIZE").stdout, "(integer) 8\n")

    def test_new_client_waits_for_daemon_lifecycle_lock(self):
        self.assertEqual(self.run_cli("SET", "initial", "value").returncode, 0)
        paths = RuntimePaths(self.directory.name)
        with paths.lock("startup"):
            process = self.start_client("SET", "after", "value",
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            with self.assertRaises(subprocess.TimeoutExpired):
                process.wait(timeout=0.2)
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stderr.decode())
        self.assertEqual(stdout, b"OK\n")

    def test_piped_repl_recovers_after_error_and_quits(self):
        result = self.run_cli(input='SET key "hello world"\nGET\nGET key\nquit\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mini-redis> ", result.stdout)
        self.assertIn("wrong number of arguments", result.stdout)
        self.assertIn('"hello world"', result.stdout)
        self.assertEqual(self.run_cli("GET", "key").stdout, '"hello world"\n')

    def test_command_line_subscription_streams_and_disconnects(self):
        subscriber = self.start_client("SUBSCRIBE", "news", stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE)
        read_until(subscriber.stdout.fileno(), b'subscribe "news"')
        self.assertEqual(self.run_cli("PUBLISH", "news", "hello world").stdout, "(integer) 1\n")
        read_until(subscriber.stdout.fileno(), b'message "news" "hello world"')
        subscriber.send_signal(signal.SIGINT)
        subscriber.wait(timeout=3)
        self.assertEqual(subscriber.returncode, 130)
        self.assertEqual(self.run_cli("PUBLISH", "news", "after").stdout, "(integer) 0\n")

    def test_terminal_repl_receives_events_without_losing_typed_input(self):
        self.run_cli("SET", "key", "value")
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        original = termios.tcgetattr(slave)
        repl = self.start_client(stdin=slave, stdout=slave, stderr=slave)
        read_until(master, b"mini-redis> ")
        os.write(master, b"SUBSCRIBE news\n")
        read_until(master, b'subscribe "news"')
        os.write(master, b"GET ke")
        read_until(master, b"GET ke")
        self.assertEqual(self.run_cli("PUBLISH", "news", "event").stdout, "(integer) 1\n")
        output = read_until(master, b"mini-redis> GET ke")
        self.assertIn(b'message "news" "event"', output)
        os.write(master, b"y\n")
        read_until(master, b'"value"')
        os.write(master, b"quit\n")
        repl.wait(timeout=3)
        self.assertEqual(repl.returncode, 0)
        self.assertEqual(termios.tcgetattr(slave), original)

    def test_stale_socket_after_crash_is_replaced_on_next_command(self):
        foreground = subprocess.Popen(
            (sys.executable, "-m", "mini_redis.daemon", "--runtime-dir", self.directory.name),
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        self.addCleanup(self.stop_client, foreground)
        read_deadline = time.monotonic() + 3
        path = os.path.join(self.directory.name, "redis.sock")
        while not os.path.exists(path) and time.monotonic() < read_deadline:
            time.sleep(0.01)
        self.assertTrue(os.path.exists(path))
        foreground.kill()
        foreground.wait(timeout=3)
        self.assertTrue(os.path.exists(path))
        result = self.run_cli("SET", "key", "after")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "OK\n")

    def test_help_does_not_start_daemon(self):
        result = subprocess.run((sys.executable, os.path.join(ROOT, "main.py"), "--help"),
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage:", result.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.directory.name, "redis.sock")))


if __name__ == "__main__":
    unittest.main()
