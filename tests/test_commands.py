import unittest

from mini_redis.commands import CommandProcessor, quote, tokenize
from mini_redis.pubsub import PubSub, Subscriber


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.broker = PubSub()
        self.processor = CommandProcessor(pubsub=self.broker)
        self.client = Subscriber("client")

    def execute(self, line):
        return self.processor.execute(line, self.client)

    def test_quotes_unicode_empty_values_and_escapes_roundtrip(self):
        for value in ("Alice", "hello world", "", "한글", 'a"b\\c\nd\t'):
            self.assertEqual(tuple(tokenize("SET key " + quote(value))), ("SET", "key", value))
            self.assertEqual(self.execute("SET key " + quote(value)).text, "OK")
            self.assertEqual(self.execute("GET key").text, quote(value))

    def test_required_outputs_and_case_insensitive_commands(self):
        self.assertEqual(self.execute("get missing").text, "(nil)")
        self.assertEqual(self.execute("KEYS").text, "(empty array)")
        self.assertEqual(self.execute("SET Key value").text, "OK")
        self.assertEqual(self.execute("EXISTS key").text, "(integer) 0")
        self.assertEqual(self.execute("KEYS").text, '1. "Key"')
        self.assertEqual(self.execute("DBSIZE").text, "(integer) 1")
        self.assertEqual(self.execute("TTL Key").text, "(integer) -1")
        self.assertEqual(self.execute("EXPIRE Key 0").text, "(integer) 1")
        self.assertEqual(self.execute("DEL Key").text, "(integer) 0")
        self.assertEqual(self.execute("INFO memory").text,
                         "used_memory:0\nmaxmemory:0\nevicted_keys:0")

    def test_errors_are_standard_and_do_not_mutate_state(self):
        self.execute("SET a old")
        for line in ("SET a", 'SET a "unfinished', "SET a bad\\"):
            self.assertTrue(self.execute(line).error)
            self.assertEqual(self.execute("GET a").text, '"old"')
        self.assertEqual(self.execute("GET").text,
                         "(error) ERR wrong number of arguments for 'GET' command")
        self.assertEqual(self.execute("HELLO").text, "(error) ERR unknown command 'HELLO'")
        for number in ("abc", "1.2", "--1", "１２", "9223372036854775808", "-1"):
            result = self.execute("CONFIG SET maxmemory " + number)
            self.assertEqual(result.text, "(error) ERR value is not an integer or out of range")
        self.assertTrue(self.execute("CONFIG GET maxmemory 1").error)
        self.assertTrue(self.execute("INFO other").error)
        self.execute("CONFIG SET maxmemory 4")
        self.assertTrue(self.execute("SET b large").text.startswith("(error) OOM"))
        self.assertEqual(self.execute("GET a").text, '"old"')

    def test_pubsub_command_outputs_and_storage_isolation(self):
        self.assertEqual(self.execute("SUBSCRIBE news").text, 'subscribe "news" (integer) 1')
        self.assertEqual(self.execute("PUBLISH news hello").text, "(integer) 1")
        self.assertEqual(self.client.pop().text, 'message "news" "hello"')
        self.assertEqual(self.execute("INFO memory").text,
                         "used_memory:0\nmaxmemory:0\nevicted_keys:0")
        self.assertEqual(self.execute("DBSIZE").text, "(integer) 0")


class PubSubTests(unittest.TestCase):
    def test_multiple_subscribers_duplicate_subscription_and_fifo(self):
        broker = PubSub()
        alice, bob = Subscriber("alice"), Subscriber("bob")
        self.assertEqual(broker.publish("news", "before"), 0)
        broker.subscribe("news", alice)
        broker.subscribe("news", alice)
        broker.subscribe("news", bob)
        broker.subscribe("other", alice)
        self.assertEqual(broker.publish("news", "first"), 2)
        self.assertEqual(broker.publish("news", "second"), 2)
        self.assertEqual(broker.publish("other", "third"), 1)
        self.assertEqual(alice.pending.size, 3)
        self.assertEqual(bob.pending.size, 2)
        self.assertEqual(alice.pop().text, 'message "news" "first"')
        self.assertEqual(alice.pop().text, 'message "news" "second"')
        self.assertEqual(alice.pop().text, 'message "other" "third"')
        self.assertEqual(alice.pending_bytes, 0)
        broker.disconnect(alice)
        self.assertEqual(broker.channels.size, 1)
        broker.disconnect(bob)
        self.assertEqual(broker.channels.size, 0)

    def test_slow_subscriber_is_bounded_and_not_counted_as_delivered(self):
        broker = PubSub()
        slow = Subscriber("slow", max_pending_bytes=30)
        fast = Subscriber("fast")
        broker.subscribe("news", slow)
        broker.subscribe("news", fast)
        self.assertEqual(broker.publish("news", "hi"), 2)
        self.assertEqual(broker.publish("news", "again"), 1)
        self.assertTrue(slow.overflowed)
        self.assertLessEqual(slow.pending_bytes, 30)
        self.assertEqual(fast.pending.size, 2)


if __name__ == "__main__":
    unittest.main()
