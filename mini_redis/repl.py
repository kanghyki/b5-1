"""단일 스레드에서 키보드 입력과 구독 이벤트를 처리하는 REPL."""

import codecs
import os
import select
import sys
import termios
import tty

from .protocol import ProtocolError


class Repl:
    """입력 중 이벤트가 오면 현재 입력을 보존하여 프롬프트를 다시 그린다."""

    prompt = "mini-redis> "

    def __init__(self, client):
        self.client = client
        self.line = ""
        self.interactive = sys.stdin.isatty() and sys.stdout.isatty()
        self.waiting = False
        self._escape = False
        self._input_decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def _render(self):
        if self.interactive:
            sys.stdout.write("\r\x1b[2K" + self.prompt + self.line)
        else:
            sys.stdout.write(self.prompt)
        sys.stdout.flush()

    def _event(self, text):
        if self.interactive:
            sys.stdout.write("\r\x1b[2K")
        elif not self.waiting:
            sys.stdout.write("\n")
        print(text, flush=True)
        if not self.waiting:
            self._render()

    def _submit(self):
        line, self.line = self.line, ""
        print(flush=True)
        if line.strip().lower() in ("exit", "quit"):
            return False
        if line.strip():
            self.waiting = True
            try:
                result = self.client.request(line, on_event=self._event)
                if result.text:
                    print(result.text, flush=True)
            finally:
                self.waiting = False
        self._render()
        return True

    def _character(self, char):
        if self._escape:
            # 기본 편집은 행 끝 입력과 삭제만 지원한다. 방향키 제어열은 무시한다.
            if char.isalpha() or char == "~":
                self._escape = False
            return True
        if char == "\x1b":
            self._escape = True
        elif char in ("\n", "\r"):
            return self._submit()
        elif char in ("\x7f", "\b"):
            self.line = self.line[:-1]
            if self.interactive:
                self._render()
        elif char == "\x04" and not self.line:
            print(flush=True)
            return False
        elif char == "\x15":
            self.line = ""
            if self.interactive:
                self._render()
        elif char.isprintable() or char == "\t":
            self.line += char
            if self.interactive:
                sys.stdout.write(char)
                sys.stdout.flush()
        return True

    def _loop(self, descriptor):
        self._render()
        while True:
            buffered = self.client.decoder.next_frame()
            if buffered is not None:
                kind, text = buffered
                if kind != "M":
                    raise ProtocolError("unexpected response while REPL is idle")
                self._event(text)
                continue
            readable, _, _ = select.select((descriptor, self.client.socket), (), ())
            if self.client.socket in readable:
                kind, text = self.client.receive()
                if kind != "M":
                    raise ProtocolError("unexpected response while REPL is idle")
                self._event(text)
            if descriptor in readable:
                data = os.read(descriptor, 4096)
                if not data:
                    if self.line:
                        self._submit()
                    else:
                        print(flush=True)
                    return 0
                for char in self._input_decoder.decode(data):
                    if not self._character(char):
                        return 0

    def run(self):
        descriptor = sys.stdin.fileno()
        original = None
        try:
            if self.interactive:
                original = termios.tcgetattr(descriptor)
                tty.setcbreak(descriptor, termios.TCSANOW)
            return self._loop(descriptor)
        finally:
            if original is not None:
                termios.tcsetattr(descriptor, termios.TCSANOW, original)
