import unittest

from mini_redis.commands import CommandProcessor, quote, tokenize


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.processor = CommandProcessor()

    def execute(self, line):
        return self.processor.execute(line)

    def test_quoted_unicode_and_empty_values(self):
        for value in ("Alice", "hello world", "", "한글"):
            self.assertEqual(tokenize("SET key " + quote(value)), ["SET", "key", value])
            self.assertEqual(self.execute("SET key " + quote(value)), "OK")
            self.assertEqual(self.execute("GET key"), quote(value))
        self.assertEqual(tokenize("SET  key   plain "), ["SET", "key", "plain"])

    def test_required_outputs_and_case_insensitive_commands(self):
        self.assertEqual(self.execute("get missing"), "(nil)")
        self.assertEqual(self.execute("KEYS"), "(empty array)")
        self.assertEqual(self.execute("SET Key value"), "OK")
        self.assertEqual(self.execute("EXISTS key"), "(integer) 0")
        self.assertEqual(self.execute("KEYS"), '1. "Key"')
        self.assertEqual(self.execute("DBSIZE"), "(integer) 1")
        self.assertEqual(self.execute("TTL Key"), "(integer) -1")
        self.assertEqual(self.execute("EXPIRE Key 0"), "(integer) 1")
        self.assertEqual(self.execute("DEL Key"), "(integer) 0")
        self.assertEqual(self.execute("INFO memory"),
                         "used_memory:0\nmaxmemory:0\nevicted_keys:0")

    def test_errors_are_standard_and_do_not_mutate_state(self):
        self.execute("SET a old")
        for line in ("SET a", 'SET a "unfinished'):
            self.assertTrue(self.execute(line).startswith("(error)"))
            self.assertEqual(self.execute("GET a"), '"old"')
        self.assertEqual(self.execute("GET"),
                         "(error) ERR wrong number of arguments for 'GET' command")
        self.assertEqual(self.execute("HELLO"), "(error) ERR unknown command 'HELLO'")
        for number in ("abc", "1.2", "--1", "１２", "9223372036854775808", "-1"):
            result = self.execute("CONFIG SET maxmemory " + number)
            self.assertEqual(result, "(error) ERR value is not an integer or out of range")
        self.assertTrue(self.execute("CONFIG GET maxmemory 1").startswith("(error)"))
        self.assertTrue(self.execute("INFO other").startswith("(error)"))
        self.execute("CONFIG SET maxmemory 4")
        self.assertTrue(self.execute("SET b large").startswith("(error) OOM"))
        self.assertEqual(self.execute("GET a"), '"old"')


if __name__ == "__main__":
    unittest.main()
