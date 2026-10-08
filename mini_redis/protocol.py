"""4바이트 길이 + 종류 1바이트 + UTF-8 본문을 사용하는 로컬 IPC 프레임."""

MAX_FRAME = 1024 * 1024
KINDS = ("Q", "R", "E", "M", "P", "S")


class ProtocolError(Exception):
    """길이, 종류, UTF-8 형식이 잘못된 메시지."""


def encode_frame(kind, text):
    if kind not in KINDS:
        raise ProtocolError("invalid message kind")
    payload = kind.encode("ascii") + text.encode("utf-8")
    if len(payload) > MAX_FRAME:
        raise ProtocolError("IPC message exceeds 1 MiB")
    return len(payload).to_bytes(4, "big") + payload


class FrameDecoder:
    """분할 수신과 여러 프레임의 동시 수신을 동일하게 처리한다."""

    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)

    def next_frame(self):
        if len(self.buffer) < 4:
            return None
        length = int.from_bytes(self.buffer[:4], "big")
        if length < 1 or length > MAX_FRAME:
            raise ProtocolError("invalid IPC message length")
        if len(self.buffer) < length + 4:
            return None
        payload = bytes(self.buffer[4:4 + length])
        del self.buffer[:4 + length]
        try:
            kind = payload[:1].decode("ascii")
            text = payload[1:].decode("utf-8")
        except UnicodeDecodeError:
            raise ProtocolError("invalid IPC text encoding") from None
        if kind not in KINDS:
            raise ProtocolError("invalid message kind")
        return kind, text
