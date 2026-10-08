"""입력 파싱과 Redis 스타일 출력. 저장소와 전송 계층에서 독립적이다."""

from .store import OutOfMemoryError, Store
from .structures.linked_list import DoublyLinkedList


class CommandError(Exception):
    """사용자 입력 오류를 표준 에러 응답으로 변환한다."""


class Result:
    def __init__(self, text, error=False):
        self.text, self.error = text, error


def quote(value):
    """공백·따옴표·개행을 포함하는 문자열을 왕복 가능한 형태로 표현한다."""
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    value = value.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return '"' + value + '"'


def tokenize(line):
    """큰따옴표와 역슬래시 이스케이프를 지원하고 토큰을 순서대로 생성한다."""
    pieces = DoublyLinkedList()
    quoted = False
    started = False
    index = 0
    while index < len(line):
        char = line[index]
        if char.isspace() and not quoted:
            if started:
                yield "".join(pieces)
                pieces = DoublyLinkedList()
                started = False
            index += 1
        elif char == '"':
            quoted = not quoted
            started = True
            index += 1
        elif char == "\\":
            index += 1
            if index == len(line):
                raise CommandError("ERR trailing escape in command")
            escaped = line[index]
            if escaped == "n":
                escaped = "\n"
            elif escaped == "r":
                escaped = "\r"
            elif escaped == "t":
                escaped = "\t"
            pieces.insert_back(escaped)
            started = True
            index += 1
        else:
            start = index
            while index < len(line):
                char = line[index]
                if char in ('"', "\\") or (char.isspace() and not quoted):
                    break
                index += 1
            pieces.insert_back(line[start:index])
            started = True
    if quoted:
        raise CommandError("ERR unterminated quoted string")
    if started:
        yield "".join(pieces)


def parse_integer(value, minimum=-(2 ** 63)):
    """부호 있는 64비트 정수 범위를 명시적으로 검증한다."""
    digits = value[1:] if value.startswith(("+", "-")) else value
    if not digits or any(char < "0" or char > "9" for char in digits):
        raise CommandError("ERR value is not an integer or out of range")
    try:
        number = int(value)
    except ValueError:
        raise CommandError("ERR value is not an integer or out of range") from None
    if number < minimum or number > 2 ** 63 - 1:
        raise CommandError("ERR value is not an integer or out of range")
    return number


class CommandProcessor:
    """검증이 끝난 명령만 실행하며 명령 오류가 상태 변경으로 이어지지 않게 한다."""

    def __init__(self, store=None, pubsub=None):
        self.store = store if store is not None else Store()
        self.pubsub = pubsub

    @staticmethod
    def _arity(tokens, expected):
        if len(tokens) != expected:
            raise CommandError(
                "ERR wrong number of arguments for '%s' command" % tokens[0].upper()
            )

    def execute(self, line, subscriber=None):
        try:
            tokens = tuple(tokenize(line))
            if not tokens:
                return Result("")
            return Result(self._execute(tokens, subscriber))
        except CommandError as error:
            return Result("(error) " + str(error), True)
        except OutOfMemoryError:
            return Result(
                "(error) OOM command not allowed when used_memory > 'maxmemory'", True
            )

    def _execute(self, tokens, subscriber):
        command = tokens[0].upper()
        store = self.store
        if command == "SET":
            self._arity(tokens, 3)
            store.set(tokens[1], tokens[2])
            return "OK"
        if command == "GET":
            self._arity(tokens, 2)
            value = store.get(tokens[1])
            return "(nil)" if value is None else quote(value)
        if command == "DEL":
            self._arity(tokens, 2)
            return "(integer) %d" % store.delete(tokens[1])
        if command == "EXISTS":
            self._arity(tokens, 2)
            return "(integer) %d" % store.exists(tokens[1])
        if command == "DBSIZE":
            self._arity(tokens, 1)
            return "(integer) %d" % store.dbsize()
        if command == "KEYS":
            self._arity(tokens, 1)
            lines = DoublyLinkedList()
            for index, key in enumerate(store.keys(), 1):
                lines.insert_back("%d. %s" % (index, quote(key)))
            return "\n".join(lines) if lines.size else "(empty array)"
        if command == "CONFIG":
            self._arity(tokens, 4)
            if tokens[1].upper() != "SET" or tokens[2].lower() != "maxmemory":
                raise CommandError("ERR expected CONFIG SET maxmemory bytes")
            store.configure_maxmemory(parse_integer(tokens[3], 0))
            return "OK"
        if command == "INFO":
            self._arity(tokens, 2)
            if tokens[1].lower() != "memory":
                raise CommandError("ERR expected INFO memory")
            return "used_memory:%d\nmaxmemory:%d\nevicted_keys:%d" % store.memory_info()
        if command == "EXPIRE":
            self._arity(tokens, 3)
            return "(integer) %d" % store.expire(tokens[1], parse_integer(tokens[2]))
        if command == "TTL":
            self._arity(tokens, 2)
            return "(integer) %d" % store.ttl(tokens[1])
        if command in ("SUBSCRIBE", "PUBLISH"):
            self._arity(tokens, 2 if command == "SUBSCRIBE" else 3)
            if self.pubsub is None or subscriber is None:
                raise CommandError("ERR Pub/Sub requires a client connection")
            if command == "SUBSCRIBE":
                count = self.pubsub.subscribe(tokens[1], subscriber)
                return "subscribe %s (integer) %d" % (quote(tokens[1]), count)
            return "(integer) %d" % self.pubsub.publish(tokens[1], tokens[2])
        raise CommandError("ERR unknown command '%s'" % tokens[0])
