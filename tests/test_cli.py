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

from mini_redis.client import Client
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

    def test_explicit_start_shared_data_errors_and_restart(self):
        self.assertEqual(self.run_cli("daemon", "status").stdout, "stopped\n")
        self.assertEqual(self.run_cli("daemon", "start").stdout, "OK\n")
        self.assertEqual(self.run_cli("SET", "키", '한 글"\\\n').stdout, "OK\n")
        self.assertEqual(self.run_cli("GET", "키").stdout, '"한 글\\"\\\\\\n"\n')
        self.assertEqual(self.run_cli("daemon", "status").stdout, "running\n")
        error = self.run_cli("GET")
        self.assertEqual(error.returncode, 1)
        self.assertIn("wrong number of arguments", error.stdout)
        self.assertEqual(self.run_cli("daemon", "stop").stdout, "OK\n")
        self.assertFalse(os.path.exists(os.path.join(self.directory.name, "redis.sock")))
        self.assertEqual(self.run_cli("GET", "키").returncode, 1)
        self.assertEqual(self.run_cli("daemon", "start").stdout, "OK\n")
        self.assertEqual(self.run_cli("GET", "키").stdout, "(nil)\n")

    def test_clients_require_a_running_daemon(self):
        for arguments in ((), ("GET", "key"), ("SET", "key", "value"),
                          ("SUBSCRIBE", "news")):
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments, input="quit\n")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn("daemon is not running", result.stderr)
                self.assertIn("daemon start", result.stderr)
                self.assertFalse(os.path.exists(os.path.join(self.directory.name, "redis.sock")))
        self.assertEqual(self.run_cli("daemon", "status").stdout, "stopped\n")

    def test_concurrent_explicit_starts_use_one_shared_daemon(self):
        starters = tuple(self.start_client("daemon", "start", stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE) for _ in range(8))
        for process in starters:
            stdout, stderr = process.communicate(timeout=6)
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertEqual(stdout, b"OK\n")
        processes = tuple(self.start_client("SET", "key%d" % index, str(index),
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                          for index in range(8))
        for process in processes:
            stdout, stderr = process.communicate(timeout=6)
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertEqual(stdout, b"OK\n")
        self.assertEqual(self.run_cli("DBSIZE").stdout, "(integer) 8\n")

    def test_daemon_start_waits_for_lifecycle_lock(self):
        paths = RuntimePaths(self.directory.name)
        with paths.lock("startup"):
            process = self.start_client("daemon", "start",
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            with self.assertRaises(subprocess.TimeoutExpired):
                process.wait(timeout=0.2)
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stderr.decode())
        self.assertEqual(stdout, b"OK\n")

    def test_piped_repl_recovers_after_error_and_quits(self):
        self.assertEqual(self.run_cli("daemon", "start").returncode, 0)
        result = self.run_cli(input='SET key "hello world"\nGET\nGET key\nquit\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mini-redis> ", result.stdout)
        self.assertIn("wrong number of arguments", result.stdout)
        self.assertIn('"hello world"', result.stdout)
        self.assertEqual(self.run_cli("GET", "key").stdout, '"hello world"\n')

    def test_command_line_subscription_streams_and_disconnects(self):
        self.assertEqual(self.run_cli("daemon", "start").returncode, 0)
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
        self.assertEqual(self.run_cli("daemon", "start").returncode, 0)
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

    def test_stale_socket_after_crash_requires_explicit_restart(self):
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
        failed = self.run_cli("SET", "key", "after")
        self.assertEqual(failed.returncode, 1)
        self.assertIn("daemon start", failed.stderr)
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self.run_cli("daemon", "start").returncode, 0)
        result = self.run_cli("SET", "key", "after")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "OK\n")

    def test_runtime_directories_isolate_servers_and_channels(self):
        other_directory = tempfile.TemporaryDirectory(prefix="mr-other-")
        self.addCleanup(other_directory.cleanup)
        other_command = self.command[:-1] + (other_directory.name,)

        def run_other(*arguments):
            return subprocess.run(other_command + arguments, cwd=ROOT,
                                  capture_output=True, text=True, timeout=6)

        self.addCleanup(run_other, "daemon", "stop")
        self.assertEqual(self.run_cli("daemon", "start").returncode, 0)
        self.assertEqual(run_other("daemon", "start").returncode, 0)
        self.assertEqual(self.run_cli("SET", "key", "A").stdout, "OK\n")
        self.assertEqual(run_other("GET", "key").stdout, "(nil)\n")
        self.assertEqual(run_other("SET", "key", "B").stdout, "OK\n")
        self.assertEqual(self.run_cli("GET", "key").stdout, '"A"\n')
        self.assertEqual(run_other("GET", "key").stdout, '"B"\n')
        with Client.open(RuntimePaths(self.directory.name)) as subscriber:
            subscriber.subscribe("news")
            self.assertEqual(run_other("PUBLISH", "news", "other").stdout, "(integer) 0\n")
            self.assertEqual(self.run_cli("PUBLISH", "news", "local").stdout, "(integer) 1\n")
            self.assertEqual(subscriber.receive_message(timeout=2).data, "local")
        self.assertEqual(self.run_cli("daemon", "stop").returncode, 0)
        self.assertEqual(run_other("GET", "key").stdout, '"B"\n')

    def test_help_does_not_start_daemon(self):
        result = subprocess.run((sys.executable, os.path.join(ROOT, "main.py"), "--help"),
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage:", result.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.directory.name, "redis.sock")))


if __name__ == "__main__":
    unittest.main()
