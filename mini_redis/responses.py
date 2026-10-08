"""데몬의 표시용 응답을 Client가 사용할 Python 값으로 변환한다."""

from .commands import CommandError, parse_integer, quote, tokenize
from .protocol import ProtocolError


class ServerError(Exception):
    """데몬이 반환한 명령 오류. 원래 에러 문자열을 보존한다."""


class Message:
    """구독 메시지의 채널과 본문을 분리하여 제공한다."""

    def __init__(self, channel, data):
        self.channel, self.data = channel, data

    @property
    def text(self):
        return "message %s %s" % (quote(self.channel), quote(self.data))


class MemoryInfo:
    """내장 맵 대신 명시적인 필드로 메모리 통계를 제공한다."""

    def __init__(self, used_memory, maxmemory, evicted_keys):
        self.used_memory = used_memory
        self.maxmemory = maxmemory
        self.evicted_keys = evicted_keys


def _number(text, minimum=-(2 ** 63)):
    try:
        return parse_integer(text, minimum)
    except CommandError:
        raise ProtocolError("invalid integer in daemon response") from None


def _tokens(text):
    try:
        return tuple(tokenize(text))
    except CommandError:
        raise ProtocolError("invalid quoted daemon response") from None


def raise_for_error(result):
    if result.error:
        raise ServerError(result.text)
    return result.text


def parse_ok(result):
    if raise_for_error(result) != "OK":
        raise ProtocolError("expected OK response")
    return True


def parse_integer_reply(result):
    text = raise_for_error(result)
    if not text.startswith("(integer) "):
        raise ProtocolError("expected integer response")
    return _number(text[len("(integer) "):])


def parse_boolean_reply(result):
    number = parse_integer_reply(result)
    if number not in (0, 1):
        raise ProtocolError("expected integer 0 or 1 response")
    return bool(number)


def _quoted(text):
    tokens = _tokens(text)
    if len(tokens) != 1 or quote(tokens[0]) != text:
        raise ProtocolError("expected quoted string response")
    return tokens[0]


def parse_value(result):
    text = raise_for_error(result)
    return None if text == "(nil)" else _quoted(text)


def parse_keys(result):
    text = raise_for_error(result)
    if text == "(empty array)":
        return ()

    def entries():
        for index, line in enumerate(text.split("\n"), 1):
            prefix = "%d. " % index
            if not line.startswith(prefix):
                raise ProtocolError("expected numbered key response")
            yield _quoted(line[len(prefix):])

    return tuple(entries())


def parse_memory_info(result):
    lines = raise_for_error(result).split("\n")
    fields = ("used_memory:", "maxmemory:", "evicted_keys:")
    if len(lines) != len(fields):
        raise ProtocolError("expected memory statistics response")
    for line, prefix in zip(lines, fields):
        if not line.startswith(prefix):
            raise ProtocolError("expected memory statistics response")
    return MemoryInfo(*(_number(line[len(prefix):], 0)
                        for line, prefix in zip(lines, fields)))


def parse_subscription(result, channel):
    tokens = _tokens(raise_for_error(result))
    if (len(tokens) != 4 or tokens[0] != "subscribe" or
            tokens[1] != channel or tokens[2] != "(integer)"):
        raise ProtocolError("expected subscription response for requested channel")
    return _number(tokens[3], 1)


def parse_message(text):
    tokens = _tokens(text)
    if len(tokens) != 3 or tokens[0] != "message":
        raise ProtocolError("expected channel and data in Pub/Sub message")
    return Message(tokens[1], tokens[2])
