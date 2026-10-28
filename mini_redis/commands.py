"""입력 파싱과 Redis 스타일 출력."""

from .store import OutOfMemoryError, Store


class CommandError(Exception):
    """사용자 입력 오류를 표준 에러 응답으로 변환한다."""


def quote(value):
    return '"' + value + '"'


def tokenize(line):
    """공백으로 토큰을 나누고, 큰따옴표 안의 공백은 값에 포함한다."""
    tokens = []
    token = ""
    quoted = False
    started = False
    for char in line:
        if char == '"':
            quoted = not quoted
            started = True
        elif char.isspace() and not quoted:
            if started:
                tokens.append(token)
                token = ""
                started = False
        else:
            token += char
            started = True
    if quoted:
        raise CommandError("ERR unterminated quoted string")
    if started:
        tokens.append(token)
    return tokens


def parse_integer(value, minimum=-(2 ** 63)):
    """부호 있는 64비트 정수 범위를 명시적으로 검증한다."""
    digits = value[1:] if value.startswith(("+", "-")) else value
    if not digits or any(char < "0" or char > "9" for char in digits):
        raise CommandError("ERR value is not an integer or out of range")
    number = int(value)
    if number < minimum or number > 2 ** 63 - 1:
        raise CommandError("ERR value is not an integer or out of range")
    return number


class CommandProcessor:
    """검증이 끝난 명령만 실행하며 명령 오류가 상태 변경으로 이어지지 않게 한다."""

    def __init__(self, store=None):
        self.store = store if store is not None else Store()

    @staticmethod
    def _arity(tokens, expected):
        if len(tokens) != expected:
            raise CommandError(
                f"ERR wrong number of arguments for '{tokens[0].upper()}' command"
            )

    def execute(self, line):
        """한 줄을 실행하고 Redis 스타일 출력 문자열을 반환한다."""
        try:
            tokens = tokenize(line)
            if not tokens:
                return ""
            return self._execute(tokens)
        except CommandError as error:
            return "(error) " + str(error)
        except OutOfMemoryError:
            return "(error) OOM command not allowed when used_memory > 'maxmemory'"

    def _execute(self, tokens):
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
            return f"(integer) {store.delete(tokens[1])}"
        if command == "EXISTS":
            self._arity(tokens, 2)
            return f"(integer) {store.exists(tokens[1])}"
        if command == "DBSIZE":
            self._arity(tokens, 1)
            return f"(integer) {store.dbsize()}"
        if command == "KEYS":
            self._arity(tokens, 1)
            lines = [f"{index}. {quote(key)}"
                     for index, key in enumerate(store.keys(), 1)]
            return "\n".join(lines) if lines else "(empty array)"
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
            used_memory, maxmemory, evicted_keys = store.memory_info()
            return (f"used_memory:{used_memory}\nmaxmemory:{maxmemory}\n"
                    f"evicted_keys:{evicted_keys}")
        if command == "EXPIRE":
            self._arity(tokens, 3)
            return f"(integer) {store.expire(tokens[1], parse_integer(tokens[2]))}"
        if command == "TTL":
            self._arity(tokens, 2)
            return f"(integer) {store.ttl(tokens[1])}"
        raise CommandError(f"ERR unknown command '{tokens[0]}'")
