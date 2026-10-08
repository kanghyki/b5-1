"""REPL, 단일 명령, 구독 스트림, 데몬 제어의 CLI 진입점."""

import os
import subprocess
import sys
import time

from .client import Client
from .protocol import ProtocolError
from .repl import Repl
from .runtime import RuntimePaths


def usage():
    print("usage: python3 main.py [--runtime-dir PATH] [COMMAND [ARG ...]]")
    print("       python3 main.py [--runtime-dir PATH] daemon start|status|stop")


def daemon_control(paths, action):
    if action == "start":
        with Client.open(paths, auto_start=True) as client:
            result = client.request(kind="P")
            print("OK" if not result.error else result.text)
            return int(result.error)
    if action == "status":
        try:
            with Client.open(paths, auto_start=False) as client:
                result = client.request(kind="P")
                if result.error:
                    print(result.text)
                    return 1
        except (FileNotFoundError, ConnectionRefusedError):
            print("stopped")
            return 1
        print("running")
        return 0
    if action == "stop":
        # 명시적 시작과 종료가 겹치지 않도록 종료 확인까지 잠금을 유지한다.
        with paths.lock("startup"):
            try:
                with Client.open(paths, auto_start=False) as client:
                    result = client.request(kind="S")
            except (FileNotFoundError, ConnectionRefusedError):
                print("(error) daemon is not running", file=sys.stderr)
                return 1
            if result.error:
                print(result.text)
                return 1
            deadline = time.monotonic() + 3
            while os.path.exists(paths.socket):
                if time.monotonic() >= deadline:
                    raise ConnectionError("daemon did not stop within 3 seconds")
                time.sleep(0.01)
        print("OK")
        return 0
    usage()
    return 2


def main(argv=None):
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments in (("--help",), ("-h",)):
        usage()
        return 0
    directory = None
    if arguments and arguments[0] == "--runtime-dir":
        if len(arguments) < 2:
            usage()
            return 2
        directory, arguments = arguments[1], arguments[2:]
    if arguments and arguments[0].lower() in ("exit", "quit") and len(arguments) == 1:
        return 0
    try:
        paths = RuntimePaths(directory)
        if arguments and arguments[0].lower() == "daemon":
            if len(arguments) != 2:
                usage()
                return 2
            return daemon_control(paths, arguments[1].lower())
        try:
            client = Client.open(paths)
        except (FileNotFoundError, ConnectionRefusedError):
            print("(error) daemon is not running; run 'daemon start' with the same runtime directory",
                  file=sys.stderr)
            return 1
        with client:
            if not arguments:
                return Repl(client).run()
            result = client.execute(*arguments, on_event=lambda text: print(text, flush=True))
            if result.text:
                print(result.text, flush=True)
            if result.error:
                return 1
            if arguments[0].upper() == "SUBSCRIBE":
                for message in client.listen():
                    print(message.text, flush=True)
            return 0
    except KeyboardInterrupt:
        print()
        return 130
    except (OSError, ValueError, ProtocolError, subprocess.SubprocessError) as error:
        print("(error) %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
