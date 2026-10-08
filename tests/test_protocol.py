import unittest

from mini_redis.protocol import FrameDecoder, MAX_FRAME, ProtocolError, encode_frame


class ProtocolTests(unittest.TestCase):
    def test_split_and_combined_frames_with_utf8(self):
        decoder = FrameDecoder()
        wire = encode_frame("Q", 'SET 키 "한 글"') + encode_frame("P", "")
        for byte in wire[:-5]:
            decoder.feed(bytes((byte,)))
        self.assertEqual(decoder.next_frame(), ("Q", 'SET 키 "한 글"'))
        self.assertIsNone(decoder.next_frame())
        decoder.feed(wire[-5:])
        self.assertEqual(decoder.next_frame(), ("P", ""))
        self.assertIsNone(decoder.next_frame())

    def test_invalid_sizes_types_and_encodings(self):
        for wire in (bytes(4), (MAX_FRAME + 1).to_bytes(4, "big"),
                     b"\x00\x00\x00\x01X", b"\x00\x00\x00\x02Q\xff"):
            decoder = FrameDecoder()
            decoder.feed(wire)
            with self.assertRaises(ProtocolError):
                decoder.next_frame()
        with self.assertRaises(ProtocolError):
            encode_frame("Q", "x" * MAX_FRAME)


if __name__ == "__main__":
    unittest.main()
