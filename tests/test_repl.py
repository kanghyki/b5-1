import io
import unittest
from contextlib import redirect_stdout

from mini_redis.repl import run


def feed(*lines):
    """input()처럼 프롬프트를 출력하고 준비된 줄을 차례로 돌려준다."""
    pending = iter(lines)

    def read(prompt):
        print(prompt, end="")
        line = next(pending, None)
        if line is None:
            raise EOFError
        return line

    return read


class ReplTests(unittest.TestCase):
    def run_repl(self, *lines):
        output = io.StringIO()
        with redirect_stdout(output):
            code = run(read=feed(*lines))
        return code, output.getvalue()

    def test_executes_commands_until_exit(self):
        code, output = self.run_repl('SET user:1 "Alice"', "", "GET user:1", "quit", "GET user:1")
        self.assertEqual(code, 0)
        self.assertEqual(output, 'mini-redis> OK\nmini-redis> mini-redis> "Alice"\nmini-redis> ')

    def test_end_of_input_exits(self):
        code, output = self.run_repl("HELLO")
        self.assertEqual(code, 0)
        self.assertEqual(output, "mini-redis> (error) ERR unknown command 'HELLO'\nmini-redis> \n")


if __name__ == "__main__":
    unittest.main()
