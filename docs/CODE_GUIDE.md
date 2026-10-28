# mini_redis 코드 해설

mini_redis의 코드는 사용자 입력, 클라이언트 통신, 서버 실행, 명령 처리, 자료구조로 나뉜다. `Client`의 명령별 메서드는 요청을 만들고 결과를 Python 값으로 반환하며, 명령의 의미와 상태 변경은 데몬에서 결정한다. 전체 프로세스 구성과 Redis 비교는 [아키텍처](ARCHITECTURE.md)를 참고한다.

## 코드 탐색 순서

사용자 명령 하나가 저장소에 도착하는 순서를 먼저 읽으면 각 파일의 역할을 이해하기 쉽다.

```text
main.py
  → cli.main()
    → Client.open()
    → Client.execute() 또는 Repl.run() → Client.request()
      → encode_frame() → Unix socket
        → Daemon._read() → Daemon._request()
          → CommandProcessor.execute()
            → Store 또는 PubSub
          ← Result
        ← Subscriber 출력 큐 → Daemon._write()
      ← Client.receive() → Result
    → 표시 또는 responses.py의 값 변환
```

자료구조를 따로 이해하려면 [linked_list.py](../mini_redis/structures/linked_list.py), [hash_map.py](../mini_redis/structures/hash_map.py), [min_heap.py](../mini_redis/structures/min_heap.py), [store.py](../mini_redis/store.py) 순서로 읽는다. Store가 세 자료구조를 어떻게 연결하는지 확인한 뒤 전송 계층을 읽으면 된다.

## main.py와 cli.py

[main.py](../main.py)는 `mini_redis.cli.main()`의 반환값을 프로세스 종료 코드로 전달한다. 실제 분기는 [cli.py](../mini_redis/cli.py)의 `main(argv=None)`에 있다. 인자를 직접 전달할 수도 있어 테스트에서 진입점 동작을 확인할 수 있다.

`main()`은 도움말과 실행 디렉터리를 처리하고, 다음 세 경로 중 하나를 선택한다.

| 인자 | 동작 |
| --- | --- |
| 명령 없음 | Client를 열고 `Repl.run()` 실행 |
| `daemon start/status/stop` | `daemon_control()`로 수명 관리 |
| 그 외 | `Client.execute(*arguments)`로 한 명령 실행 |

`--runtime-dir`은 명령 앞에 지정한다. 단독 `--help`나 `-h`, 단독 `exit` 또는 `quit`은 연결 전에 처리한다. 일반 명령은 먼저 `Client.open(paths)`로 연결을 시도하고, 성공한 Client를 `with client` 블록에서 사용해 종료 시 연결을 닫는다. 이 연결 단계에서 소켓이 없거나 연결이 거절되면 명령과 REPL을 실행하지 않고 시작 안내를 출력한다.

일반 응답은 `Result.text`를 출력하고, 오류는 종료 코드 `1`로 전달한다. 단일 명령이 `SUBSCRIBE`라면 구독 확인 출력 후 `client.listen()`을 계속 순회한다. `Ctrl+C`는 `130`, 사용법 오류는 `2`, 정상 실행은 `0`이다. REPL에서 발생한 명령 오류는 REPL이 출력하고 다음 입력을 받으므로 REPL 전체를 종료시키지 않는다.

`daemon_control()`의 `start`만 `Client.open(paths, auto_start=True)`를 사용해 데몬을 시작할 수 있고 `P` 프레임으로 상태를 확인한다. 일반 명령과 REPL은 연결만 시도하며 데몬이 없으면 시작 안내와 종료 코드 1을 반환한다. `status`는 연결로 확인해 `running` 또는 `stopped`를 출력한다. `stop`은 `startup` 잠금 안에서 `S` 프레임을 보내고 소켓 파일이 사라질 때까지 최대 3초 기다린다.

## runtime.py와 프로세스 잠금

[RuntimePaths](../mini_redis/runtime.py)는 실행 디렉터리와 소켓 경로를 결정한다. 명시 경로, 환경 변수, 사용자별 임시 디렉터리 순으로 선택하고 절대 경로로 바꾼다. 디렉터리의 소유자와 `0700` 권한, 소켓 경로 길이를 검증한다.

`lock(name, blocking=True)`은 문맥 관리자로 파일 디스크립터를 열고 `fcntl.flock()`을 적용한다. `finally`에서 디스크립터를 닫으므로 예외가 나도 잠금이 해제된다. 프로세스가 비정상 종료되어도 운영체제가 잠금을 해제한다. 잠금 파일이 남아 있다는 사실만으로 잠금이 유지되는 것은 아니다.

| 잠금 | 획득 위치 | 보유 기간 |
| --- | --- | --- |
| `startup.lock` | 명시적 start 경로의 `Client.open()`과 CLI의 stop | 시작 절차 또는 종료 확인 동안 |
| `daemon.lock` | `Daemon.serve()` | 서버 실행 전체 |

잠금 파일은 일반 파일이며 현재 사용자 소유여야 한다. 가능한 환경에서는 `O_NOFOLLOW`로 심볼릭 링크를 따라 열지 않는다. 데몬 잠금은 비차단으로 획득해 중복 실행이 즉시 실패하도록 한다.

## protocol.py와 프레임 복원

[encode_frame(kind, text)](../mini_redis/protocol.py)는 종류를 검증하고 `종류 1바이트 + UTF-8 텍스트`를 만든다. 본문이 1 MiB를 넘으면 `ProtocolError`를 발생시킨다. 앞에 4바이트 big-endian 길이를 붙인 결과가 소켓으로 전송된다.

`FrameDecoder`는 받은 바이트를 `bytearray`에 보관한다. `next_frame()`의 순서는 다음과 같다.

1. 길이 접두사 4바이트가 없으면 `None`을 반환한다.
2. 접두사를 읽어 본문 길이가 1부터 1 MiB인지 검증한다.
3. 본문이 아직 다 오지 않았으면 `None`을 반환한다.
4. 완성된 프레임을 버퍼에서 제거하고 종류와 UTF-8 텍스트를 디코딩한다.
5. 종류를 검증하고 `(kind, text)`를 반환한다.

UTF-8 문자의 중간에서 수신이 나뉘어도 전체 본문을 모은 뒤 디코딩하므로 처리할 수 있다. 여러 프레임이 한 번에 도착하면 반복 호출로 하나씩 꺼낸다.

```python
from mini_redis.protocol import FrameDecoder, encode_frame

frame = encode_frame("Q", 'SET name "홍길동"')
decoder = FrameDecoder()
decoder.feed(frame[:3])
assert decoder.next_frame() is None
decoder.feed(frame[3:])
assert decoder.next_frame() == ("Q", 'SET name "홍길동"')
```

프레임 디코더는 메시지 경계와 인코딩만 검증한다. `Q`가 요청인지, `M`이 이벤트인지와 같은 방향별 의미는 Daemon과 Client가 검사한다. 프레임 본문이 명령인지 응답인지에 따라 뒤의 파서도 달라진다.

## client.py

### 연결과 명시적 시작

[Client._connect(paths)](../mini_redis/client.py)는 Unix stream socket을 만들고 5초 타임아웃을 설정한 뒤 연결한다. 실패 시 소켓을 닫고 오류를 다시 전달한다.

`Client.open(paths=None, auto_start=False)`는 기본적으로 연결만 수행하고 소켓이 없거나 연결이 거절되면 예외를 전달한다. CLI에서 일반 명령과 REPL은 이 기본값을 사용한다.

`daemon start`는 명시적으로 `auto_start=True`를 전달한다. 이 경로는 `startup` 잠금 안에서 먼저 연결을 시도하고, 소켓이 없거나 연결이 거절되면 현재 Python 실행 파일로 `mini_redis.daemon` 모듈을 `--background`와 함께 실행한다. 실행 명령에는 최대 5초 제한이 있고, 이후 최대 3초 동안 20ms 간격으로 소켓 연결을 재시도한다. 권한 오류 등 다른 연결 오류에서는 서버를 새로 시작하지 않는다.

`auto_start=True` 옵션은 시작 경로를 재사용하기 위해 Client에 남아 있지만, 일반 CLI와 REPL은 전달하지 않는다. 기본 `Client.open()`도 서버를 생성하지 않는다. 이미 실행 중인 데몬에 `daemon start`를 다시 호출하면 기존 연결을 사용하므로 저장된 데이터와 구독 상태를 초기화하지 않는다.

이미 열린 Client는 서버가 끊어졌을 때 자동으로 다시 연결되지 않는다. `close()`는 소켓과 로컬 이벤트 큐를 정리하며 `with` 문도 같은 정리를 수행한다.

별도 인스턴스를 사용하는 호출 코드는 시작한 서버의 실행 디렉터리를 지정한다. 아래 코드는 먼저 `python3 main.py --runtime-dir /tmp/mini-redis-a daemon start`를 실행한 뒤 사용한다.

```python
from mini_redis.client import Client
from mini_redis.runtime import RuntimePaths

with Client.open(RuntimePaths("/tmp/mini-redis-a")) as client:
    print(client.get("name"))
```

### 전송 API와 명령별 API

| API | 입력과 반환 | 사용 목적 |
| --- | --- | --- |
| `request(text, kind="Q", on_event=None)` | 명령 원문 → `Result` | REPL 또는 내부 제어 프레임 |
| `execute(*arguments, on_event=None)` | 나누어진 인자 → `Result` | 셸 인자, 공통 요청 생성 |
| `set`, `get`, `publish` 등 | Python 인자 → Python 값 | 호출 코드에서 명령과 결과 사용 |

`execute()`는 각 인자를 `str()`로 변환한 뒤 `quote()`로 인용해 한 줄로 합친다. 명령 종류에 따른 분기나 상태 변경은 하지 않는다. 명령별 메서드는 `execute()`와 응답 파서를 연결하는 짧은 어댑터다.

| Client 메서드 | 데몬 명령 | 반환값 |
| --- | --- | --- |
| `set(key, value)` | `SET` | 성공 시 `True` |
| `get(key)` | `GET` | `str` 또는 `None` |
| `delete(key)` | `DEL` | 삭제 수 `int` |
| `exists(key)` | `EXISTS` | `bool` |
| `dbsize()` | `DBSIZE` | 키 수 `int` |
| `keys()` | `KEYS` | 순서가 보장되지 않는 `tuple` |
| `configure_maxmemory(byte_limit)` | `CONFIG SET maxmemory` | 성공 시 `True` |
| `memory_info()` | `INFO memory` | `MemoryInfo` |
| `expire(key, seconds)` | `EXPIRE` | 설정 또는 즉시 삭제 성공 시 `True`, 키 없음은 `False` |
| `ttl(key)` | `TTL` | 남은 초, `-1`, `-2` 중 `int` |
| `publish(channel, message)` | `PUBLISH` | 큐에 추가한 구독자 수 `int` |
| `subscribe(channel)` | `SUBSCRIBE` | 이 연결의 구독 채널 수 `int` |

저장소 검증은 서버가 담당한다. 예를 들어 음수 메모리 제한은 Client가 별도 정책으로 처리하지 않고 데몬의 오류를 받는다. 메서드 입력은 문자열화되므로 임의의 `bytes`를 원본 바이트 데이터로 저장하는 API는 아니다.

```python
from mini_redis.client import Client, ServerError

with Client.open() as client:
    assert client.set("user:1", "Alice Smith") is True
    assert client.get("user:1") == "Alice Smith"
    assert client.exists("user:1") is True
    assert client.get("missing") is None
    info = client.memory_info()
    print(info.used_memory, info.maxmemory, info.evicted_keys)
    try:
        client.configure_maxmemory(-1)
    except ServerError as error:
        print(error)
```

이 예시는 프로젝트 루트에서 `python3 main.py daemon start`로 기본 데몬을 먼저 시작한 뒤 실행하며 키를 추가한다. `with` 종료는 Client만 닫으므로 서버 종료는 별도의 `daemon stop`으로 수행한다. 아래 Pub/Sub 예시도 실행 중인 기본 데몬을 사용한다.

### 응답 사이의 이벤트 처리

`receive()`는 먼저 디코더에서 완성된 프레임을 꺼낸다. 없으면 최대 65,536바이트를 수신해 버퍼에 추가한다. 소켓이 빈 바이트를 반환하면 서버가 연결을 닫은 것으로 판단하고 `ConnectionError`를 발생시킨다.

`request()`는 `sendall()`로 프레임을 보내고 다음 분기를 반복한다.

```text
M → on_event가 있으면 즉시 호출
    없으면 _events에 보관
    응답을 기다리며 계속 읽기
R → Result(body, False) 반환
E → Result(body, True) 반환
그 외 → ProtocolError
```

`_events`는 FIFO 이중 연결 리스트이고 프레임 크기 기준 4 MiB를 넘으면 연결을 닫는다. 응답을 기다리던 중 버퍼 초과가 발생했을 때 연결까지 닫는 이유는, 아직 읽지 않은 응답이 다음 요청의 응답으로 잘못 해석되는 것을 막기 위해서다.

`receive_message(timeout=None)`은 저장된 이벤트를 먼저 반환한다. 큐가 비었으면 소켓 타임아웃을 지정값으로 바꾸고 `M` 프레임을 기다린다. 기본 `None`은 무기한 대기이며, `finally`에서 기존 타임아웃을 복구한다. `listen()`은 이 동작을 반복하는 generator다.

```python
from mini_redis.client import Client

with Client.open() as subscriber, Client.open() as publisher:
    assert subscriber.subscribe("news") == 1
    print(publisher.publish("news", "hello"))
    message = subscriber.receive_message(timeout=2)
    print(message.channel)  # news
    print(message.data)     # hello
```

`subscribe()`는 구독 확인 응답을 받으면 반환한다. 메시지 수신은 `receive_message()`나 `listen()`으로 이어서 수행한다. 같은 Client에서 여러 채널을 구독할 수도 있다. `listen()`을 다른 스레드에서 실행하면서 같은 Client로 요청하는 사용법은 지원하지 않으므로 지속 수신 연결과 발행 연결을 나누어 사용한다.

## responses.py

[responses.py](../mini_redis/responses.py)는 표시용 응답을 호출 코드가 사용할 값으로 바꾼다. 현재 프로토콜은 타입별 RESP 값 대신 텍스트 본문을 보내기 때문에 별도 변환이 필요하다.

`raise_for_error(result)`는 `Result.error`이면 원문을 담은 `ServerError`를 발생시킨다. 저수준 `request()`와 `execute()`는 오류를 `Result`로 반환하지만, 명령별 메서드는 이 함수를 거쳐 예외로 전달한다.

| 파서 | 검증과 변환 |
| --- | --- |
| `parse_ok` | 정확히 `OK`인지 검사 후 `True` |
| `parse_integer_reply` | `(integer) ` 접두사와 정수 범위 검사 |
| `parse_boolean_reply` | 정수 값이 0 또는 1인지 검사 후 `bool` |
| `parse_value` | `(nil)`은 `None`, 그 외 인용 문자열 복원 |
| `parse_keys` | 빈 배열 또는 1부터 연속된 번호와 인용 문자열 검사 |
| `parse_memory_info` | 세 필드의 이름, 순서, 비음수 정수 검사 |
| `parse_subscription` | 구독 확인 종류, 요청 채널, 양수 채널 수 검사 |
| `parse_message` | `message`와 채널 및 본문 세 토큰 검사 |

`_quoted()`는 토큰 하나를 얻은 뒤 다시 `quote()`한 결과가 원문과 같은지 검사한다. 따라서 따옴표가 빠진 응답을 정상 문자열로 오인하지 않는다. `_tokens()`와 `_number()`는 공통 토큰화와 정수 검증을 재사용하면서 잘못된 서버 응답을 `CommandError` 대신 `ProtocolError`로 바꾼다.

`Message`는 `channel`, `data` 속성과 표시용 `text` 속성을 가진다. `MemoryInfo`는 `used_memory`, `maxmemory`, `evicted_keys` 속성을 가진다. 필드가 정해진 객체를 사용해 내장 맵 없이 결과를 제공한다.

## daemon.py

### Connection과 서버 초기화

[Connection](../mini_redis/daemon.py)은 소켓, 파일 디스크립터 문자열 키, Subscriber, FrameDecoder, 전송 중인 프레임과 offset을 묶는다. 소켓이 재사용한 파일 디스크립터와 구독자 식별자가 충돌하지 않도록 구독자 ID는 증가하는 별도 순번을 사용한다.

`Daemon`은 연결들을 해시맵에 저장하고 `PubSub` 하나와 `CommandProcessor` 하나를 만든다. CommandProcessor가 만드는 Store가 이 서버의 공유 저장소다. 클라이언트마다 Store를 새로 만들지 않는다.

`_bind()`는 서버 잠금을 획득한 상태에서 남은 소켓을 검사하고 Unix socket을 생성한다. 현재 사용자 소유의 소켓만 교체하고 일반 파일은 거부한다. 소켓은 `listen(64)`와 비차단 모드로 설정한다.

### 읽기와 명령 실행

`_accept()`는 루프 한 번에 최대 64개 연결을 받아 비차단으로 바꾸고 연결 맵에 등록한다. `_read()`는 한 번 수신한 바이트를 해당 Connection의 디코더에 넣고 완성된 프레임을 반복 처리한다. 부분 프레임은 그 연결의 버퍼에 남는다.

`_request()`는 `Q`를 CommandProcessor에 전달하고 `P`와 `S`를 내부 제어로 처리한다. `Q`를 처리할 때 Subscriber도 함께 전달하므로 명령 처리기가 현재 연결을 구독자로 등록할 수 있다.

`_respond()`는 `Result`를 `R` 또는 `E` 출력 큐 항목으로 만든다. 응답이 프레임 제한을 넘으면 작은 오류 응답으로 대체한다. `KEYS`처럼 출력이 크게 늘어나는 명령도 한 응답 프레임을 사용하므로 결과를 여러 프레임으로 나누지는 않는다.

잘못된 프레임과 소켓 오류는 `_close_connection()`으로 이어진다. 이 함수는 PubSub 구독을 정리하고 연결 맵에서 제거한 뒤 소켓을 닫는다. 사용자가 잘못된 명령을 보낸 경우에는 CommandProcessor의 오류 응답을 보내고 연결을 유지한다.

### 부분 쓰기와 이벤트 루프

`_write()`는 출력 큐 맨 앞 Delivery를 프레임으로 만들어 `connection.outgoing`에 보관한다. `send()`가 일부 바이트만 보내면 `offset`을 늘리고, 다음 호출에서 나머지를 보낸다. 프레임 전체를 보낸 뒤에만 큐에서 항목을 제거한다.

한 번의 호출에서 최대 65,536바이트를 쓰며 `BlockingIOError`는 연결 오류가 아니라 다음 쓰기 가능 시점까지 기다리라는 의미로 처리한다. 느린 연결 때문에 전체 서버가 블로킹되지 않도록 한다.

`_loop()`의 반복 순서는 다음과 같다.

1. Store에서 만료된 키를 정리한다.
2. 출력 큐가 넘친 연결을 닫는다.
3. 읽을 소켓과 출력이 대기 중인 소켓 목록을 만든다.
4. 다음 TTL과 종료 마감 시간을 고려해 최대 1초의 대기 시간을 계산한다.
5. `select.select()`를 호출한다.
6. 수락 또는 읽기를 처리하고, 쓸 수 있는 연결에 대기 출력을 보낸다.

종료 중에는 새 읽기를 처리하지 않고 출력이 비거나 2초 마감 시간이 되면 루프를 끝낸다. `serve()`는 전체 실행 동안 데몬 잠금을 유지하며 `finally`에서 연결, listener, 자신이 만든 소켓 파일과 신호 처리기를 정리한다.

모듈의 `main()`은 `--runtime-dir PATH [--background]`를 처리한다. 백그라운드 옵션이 있으면 부모는 반환하고 자식은 `setsid()` 후 `serve()`를 수행한다. 별도 데몬 감독 프로세스를 만드는 구현은 아니다.

## commands.py

### 인용과 토큰화

[quote(value)](../mini_redis/commands.py)는 역슬래시, 큰따옴표, 개행, carriage return, 탭을 이스케이프하고 문자열을 큰따옴표로 감싼다. 역슬래시부터 처리해 뒤에 추가하는 이스케이프가 다시 변환되지 않게 한다.

`tokenize(line)`은 문자열을 순회하며 인용 여부, 토큰 시작 여부, 현재 위치를 관리한다. 인용 밖의 공백은 구분자이고, 큰따옴표는 인용 상태를 바꾼다. `\n`, `\r`, `\t`는 대응 문자로 바꾸며 다른 역슬래시 이스케이프는 다음 문자 자체로 처리한다. 작은따옴표는 일반 문자다.

`started`는 빈 문자열 `""`도 토큰으로 보존하기 위해 필요하다. 토큰 조각은 DoublyLinkedList에 모았다가 합친다. 끝에 단독 역슬래시가 남거나 큰따옴표가 닫히지 않으면 `CommandError`다.

`parse_integer()`는 선택적인 부호와 ASCII 숫자만 허용하고 signed 64-bit 범위를 검사한다. `CONFIG SET maxmemory`는 최솟값 0을 추가 적용한다. 정수처럼 보이는 실수나 범위를 초과한 값은 상태 변경 전에 거부한다.

### 명령 실행과 표시 형식

`CommandProcessor.execute()`는 토큰을 만든 후 `_execute()`로 전달한다. 빈 입력은 빈 Result다. `_execute()`는 명령명을 대문자로 바꿔 비교하므로 명령명은 대소문자를 구분하지 않지만 키, 값, 채널은 구분한다. `_arity()`로 정확한 인자 수를 검사한다.

| 입력 | 상태 처리 | 응답 예시 |
| --- | --- | --- |
| `SET key value` | 저장 및 LRU 갱신 | `OK` |
| `GET key` | 조회, 성공 시 LRU 갱신 | `"value"` 또는 `(nil)` |
| `DEL key` | 삭제 | `(integer) 1` 또는 0 |
| `EXISTS key` | 존재 확인 | `(integer) 1` 또는 0 |
| `DBSIZE` | 키 개수 | `(integer) 3` |
| `KEYS` | 전체 키 순회 | `1. "key"` 형태 또는 `(empty array)` |
| `CONFIG SET maxmemory bytes` | 제한 설정 | `OK` |
| `INFO memory` | 메모리 통계 조회 | 세 줄의 필드와 값 |
| `EXPIRE key seconds` | 만료 설정 또는 즉시 삭제 | `(integer) 1` 또는 0 |
| `TTL key` | 만료까지 남은 초 | `(integer) 9`, -1 또는 -2 |
| `SUBSCRIBE channel` | 현재 연결 등록 | `subscribe "channel" (integer) 1` |
| `PUBLISH channel message` | 구독자 출력 큐에 이벤트 추가 | `(integer) 2` |

명령 오류는 `Result("(error) ...", True)`, 저장 불가능한 단일 항목은 OOM Result로 변환한다. Pub/Sub 명령에는 브로커와 Subscriber가 모두 필요하다. Store만 사용하는 CommandProcessor에 구독 명령을 보내면 연결이 필요하다는 오류를 반환한다.

이 단계는 표시용 문자열도 생성한다. 실제 Redis는 RESP 응답 타입을 전송하고 `redis-cli`가 이를 표시하므로, 서버의 표시 문자열을 다시 해석하는 이 프로젝트와 형식의 책임이 다르다. [Redis 프로토콜 명세](https://redis.io/docs/latest/develop/reference/protocol-spec/)

## store.py

### Entry와 세 인덱스

[Entry](../mini_redis/store.py)는 `key`, `value`, `byte_size`, `lru_node`, `expiry`를 가진다. 마지막 두 필드는 데이터 복사본이 아니라 노드와 힙 항목의 직접 참조다.

Store의 `_entries`는 키에서 Entry를 찾고, `_lru`는 앞에서 뒤로 최근 접근 순서를 보관하며, `_expiry`는 TTL이 있는 Entry만 만료 시각 순서로 관리한다. `_delete(entry)`는 이 세 구조와 메모리 합계를 함께 갱신한다. 삭제와 만료, 메모리 제거가 이 함수를 공유하므로 정리 누락을 줄인다.

### set과 get

`set()`은 다음 순서로 처리한다.

1. 이미 만료된 키를 정리한다.
2. 키와 값의 UTF-8 길이로 새 항목 크기를 계산한다.
3. 항목 하나가 양수 maxmemory보다 크면 OOM으로 끝낸다.
4. 새 키는 Entry를 만들고 LRU 앞과 해시맵에 삽입한다.
5. 기존 키는 TTL을 제거하고 이전 바이트 수를 빼며 값과 크기를 갱신하고 LRU 앞으로 이동한다.
6. 새 바이트 수를 더한다.
7. 전체 크기가 제한을 넘으면 LRU 뒤부터 제거하고 `evicted_keys`를 증가시킨다.

OOM 검사를 먼저 하므로 기존 키를 너무 큰 값으로 덮어쓰는 실패가 기존 값, TTL, 접근 순서를 변경하지 않는다. 정상적인 덮어쓰기는 TTL을 제거한다. `get()`은 만료 정리 후 Entry가 있으면 LRU 앞으로 옮기고 값을 반환하며, 없으면 `None`이다.

예를 들어 LRU가 `[c, b, a]`일 때 `GET a`는 `[a, c, b]`를 만든다. 이후 새 키 `d`를 저장해 한 키를 제거해야 하면 `b`가 제거된다. `EXISTS b`만 호출했다면 순서는 유지된다.

### 메모리 통계와 단순 조회

`delete()`는 키를 찾아 공통 삭제 후 1을 반환하고, 없으면 0이다. `exists()`는 정수 0 또는 1, `dbsize()`는 해시맵 크기, `keys()`는 키 generator를 반환한다. CommandProcessor가 generator를 소비해 표시 문자열을 만들고, Client의 `keys()`는 그 응답을 tuple로 바꾼다.

`configure_maxmemory()`는 음수를 거부하고 제한을 저장한다. 기존 키의 LRU 제거는 다음 성공한 `set()`에서 수행한다. `memory_info()`는 `(used_memory, maxmemory, evicted_keys)` 튜플을 반환한다. 이 메서드들도 호출 시 만료된 키를 정리한다.

### 만료 처리

`expire()`는 키가 없으면 0, 0 이하 시간은 즉시 삭제 후 1을 반환한다. 양수 시간은 `_clock() + seconds`를 deadline으로 사용한다. 기존 TTL이 없으면 힙에 추가하고 있으면 같은 HeapItem의 우선순위를 갱신한다.

`purge_expired()`는 현재 시각을 한 번 읽고, 힙 최솟값이 그 시각 이하인 동안 해당 Entry를 `_delete()`한다. `_delete()`가 힙 항목도 제거하므로 따로 pop해서 중복 제거하지 않는다. `next_expiry_delay()`는 데몬의 다음 대기 시간 계산에 사용된다.

`ttl()`은 만료 정리 후 키 없음 -2, TTL 없음 -1을 반환한다. TTL이 있으면 남은 초를 정수로 버림하고 0 이상으로 제한한다. 시계는 생성자로 주입 가능하므로 테스트에서는 실제 대기 없이 시각을 진행시킬 수 있다.

## 직접 구현한 자료구조

### DoublyLinkedList

[이중 연결 리스트](../mini_redis/structures/linked_list.py)는 head와 tail 센티널 사이에 데이터 노드를 둔다. 센티널은 실제 데이터가 아닌 경계 노드이며 빈 리스트도 head와 tail이 연결되어 있다.

```text
head ⇄ 최근 접근 Entry ⇄ ... ⇄ 오래된 Entry ⇄ tail
```

`Node`의 `owner`는 소속 리스트를 나타낸다. `remove_node()`는 다른 리스트의 노드와 센티널 삭제를 거부하고 양쪽 이웃을 연결한 뒤 제거된 노드의 참조를 비운다. `move_to_front()`는 기존 노드를 제거했다가 앞에 다시 연결하므로 Entry의 노드 참조가 유지된다.

앞뒤 삽입, 앞뒤 조회 및 제거, 알고 있는 노드의 제거와 이동은 O(1)이다. 노드를 값으로 검색하는 기능은 없다. `nodes()`는 yield 전에 다음 노드를 기억해 순회 중 현재 노드 삭제를 지원한다. 임의의 다른 노드를 삭제하는 모든 순회 변경까지 보장하는 것은 아니다.

이 리스트는 LRU 외에 해시 충돌 체인, 서버 출력 FIFO, Client 이벤트 FIFO, 토큰 조각과 KEYS 출력 조립에도 재사용한다.

### HashMap

[해시맵](../mini_redis/structures/hash_map.py)은 문자열 키를 UTF-8로 인코딩하고 64비트 FNV-1a 해시를 계산한다. 각 바이트에 XOR와 곱셈을 적용하고 64비트로 제한한 뒤 버킷 수로 나눈 나머지를 인덱스로 사용한다. Python 내장 `hash()`에 의존하지 않는다.

같은 버킷에 들어간 Pair들은 DoublyLinkedList로 연결한다. `_find()`는 해당 버킷에서 실제 키를 비교하므로 해시 충돌이 나도 다른 키의 값을 반환하지 않는다. `contains()`는 값이 `None`인 경우도 존재 여부를 구별할 수 있다.

`put()`은 기존 키의 값만 바꾸거나 새 Pair를 삽입한다. `size * 4 > capacity * 3`, 즉 부하율 0.75 초과 시 버킷을 두 배로 만들고 전체 Pair를 다시 배치한다. 확장 시 Pair는 재사용하되 버킷 연결 노드는 새로 만든다. 기존 키 갱신은 size를 늘리지 않으며 삭제 시 버킷을 줄이지 않는다.

문자열 길이를 별도로 고려하면 해시 계산 자체가 UTF-8 바이트 수에 비례한다. 충돌이 적을 때 조회는 평균 O(1)이지만 한 버킷에 키가 몰리면 O(N)이고 확장은 일시에 전체 항목을 재배치한다. 실제 Redis의 내부 해시 구현이나 확장 전략을 그대로 옮긴 것은 아니다.

### MinHeap

[최소 힙](../mini_redis/structures/min_heap.py)은 배열의 앞 `size`개 위치를 사용한다. 부모 인덱스는 `(i - 1) // 2`, 자식은 `2i + 1`, `2i + 2`다. 부모의 우선순위가 자식 이하라는 관계를 유지해 배열 맨 앞에서 가장 빠른 만료 항목을 찾는다.

`HeapItem`은 `priority`, `value`, 현재 `index`를 가진다. `_swap()`은 배열 교환과 두 항목의 인덱스 갱신을 함께 수행한다. `push()`는 끝에 넣고 위로 올리며 공간이 모자라면 두 배 배열을 직접 만들고 복사한다.

`remove(item)`은 직접 참조와 index로 항목을 찾고 마지막 항목으로 빈자리를 메운다. 교체 항목을 위 또는 아래로 이동시켜 힙 관계를 복구하고 제거한 항목의 index를 -1로 만든다. `_validate()`는 인덱스 범위와 배열 위치의 객체 동일성을 확인해 잘못된 참조를 거부한다.

`update()`도 동일한 항목의 시각을 바꾸고 위아래 이동으로 복구한다. 따라서 EXPIRE를 반복하거나 키를 삭제해도 오래된 만료 항목을 별도 청소해야 하는 구조가 아니다. 조회는 O(1), 추가와 갱신 및 임의 삭제는 일반적으로 O(log T)이며 배열 확장은 별도 O(T) 비용이 있다.

### 전체 명령 비용

N은 저장 키 수, T는 TTL 키 수, E는 이번 호출에서 정리할 만료 키 수, V는 제거할 LRU 키 수, S는 채널 구독자 수다. 아래는 해시맵 평균 성능을 가정한 주요 비용이며 문자열 인코딩과 응답 조립 비용은 별도로 발생한다.

| 작업 | 주요 비용 |
| --- | --- |
| 기본 키 조회와 LRU 이동 | 평균 O(1) |
| TTL 갱신 또는 TTL이 있는 키 삭제 | O(log T) |
| 만료 키 정리 | O(E log T) |
| 메모리 초과 제거 | TTL이 없는 키는 평균 O(V), TTL이 있으면 힙 제거 비용 추가 |
| KEYS | 버킷과 항목 순회, 결과 문자열 생성 |
| PUBLISH | 구독자 S명 순회와 메시지 크기 계산 및 큐 추가 |

해시맵은 삭제 후 축소하지 않으므로 전체 순회 비용에는 현재 항목 수뿐 아니라 유지 중인 버킷 수도 영향을 준다. 이 비용과 만료 정리는 명령의 전체 실행 시간에 포함된다.

## pubsub.py

[Delivery](../mini_redis/pubsub.py)는 프레임 종류와 본문, `5 + UTF-8 본문 길이`의 바이트 수를 가진다. 5는 길이 접두사 4바이트와 종류 1바이트다.

`Subscriber`는 식별자, 구독 채널 맵, FIFO 출력 큐, 대기 바이트 합계, overflow 상태를 가진다. 구독하지 않은 일반 연결에도 Subscriber가 있어서 일반 응답과 이벤트를 하나의 큐로 관리한다.

`enqueue()`는 프레임 크기와 기본 4 MiB 대기 제한을 검사한다. 초과하면 overflow 상태를 설정하고 추가를 거부한다. `pop()`은 전송이 완료된 앞 항목을 제거하고 크기를 차감한다. 부분 전송 중인 항목도 완료 전까지 큐 크기에 포함된다.

`PubSub.subscribe()`는 채널의 구독자 맵과 Subscriber의 채널 맵 양쪽에 등록한다. 같은 ID로 put하므로 중복 구독이 생기지 않으며 반환값은 이 연결의 서로 다른 채널 수다.

`publish()`는 채널을 찾아 `message "channel" "data"` 본문을 만들고 각 구독자의 큐에 `M`을 추가한다. 성공한 enqueue 수를 반환한다. 구독자 없음이나 이미 overflow된 구독자는 성공 수에 포함하지 않는다. 이 값은 클라이언트에서의 처리 완료 횟수가 아니다.

`disconnect()`는 Subscriber가 속한 모든 채널에서 ID를 제거하고, 마지막 구독자가 나간 채널 자체도 제거한다. 채널 목록과 출력 큐도 비운다. 구독 취소 명령은 없으므로 연결 종료가 전체 구독 해제 경로다.

## repl.py

[Repl](../mini_redis/repl.py)은 입력 문자열 `line`, 터미널 여부 `interactive`, 명령 응답 대기 여부 `waiting`을 관리한다. 입력은 바이트 단위로 나뉠 수 있으므로 incremental UTF-8 decoder를 사용한다.

`run()`은 입력과 출력이 모두 터미널일 때 기존 설정을 저장하고 cbreak 모드로 전환한다. `finally`에서 원래 설정을 복원한다. `_loop()`는 디코더에 이미 완성된 메시지가 있는지 먼저 확인하고, 없으면 표준 입력과 서버 소켓을 함께 `select()`로 기다린다. 이미 수신한 메시지를 버퍼에 둔 채 새 소켓 데이터만 기다리는 문제를 피한다.

`_character()`는 Enter, Backspace, 빈 입력에서 Ctrl+D, Ctrl+U와 일반 문자를 처리한다. 방향키 제어열은 무시하고 행 끝 입력과 삭제를 지원한다. `_submit()`은 `exit`와 `quit`을 로컬 종료로 처리하거나 입력 원문을 `request()`에 보낸다.

구독 메시지의 `_event()`는 현재 화면 행을 지우고 메시지를 출력한 뒤 입력 줄을 복원한다. 응답 대기 중에는 `waiting=True`로 프롬프트 재출력을 미루고 request의 콜백으로 메시지를 표시한다. 요청을 처리하는 동안에도 키보드 입력을 별도 스레드에서 읽는 구조는 아니다.

## 오류와 제한

| 구분 | 발생 예 | 호출자가 받는 결과 |
| --- | --- | --- |
| 연결 대상 없음 | 소켓 파일 없음 또는 연결 거절 | Client.open은 FileNotFoundError 또는 ConnectionRefusedError, 일반 CLI는 시작 안내와 종료 코드 1 |
| 명령 오류 | 잘못된 인자 수, 정수, 알 수 없는 명령 | raw API는 오류 Result, 명령별 API는 ServerError |
| 저장 불가 | 단일 키와 값이 maxmemory 초과 | OOM 오류 Result 또는 ServerError |
| 프로토콜 오류 | 프레임 길이, 인코딩, 예상하지 않은 응답 형식 | ProtocolError 또는 서버의 연결 종료 |
| 연결 종료 | 서버 종료, 큐 초과 등 | EOF 수신 시 ConnectionError |
| 대기 시간 초과 | 요청 응답 또는 지정한 메시지 대기 시간 초과 | socket.timeout |

같은 Client에서 요청은 순차적으로 수행한다. request ID, 자동 재연결, 요청 재시도는 없다. 특히 전송 후 응답 타임아웃은 서버가 명령을 실행했는지 판단할 수 없고, 늦게 도착한 응답이 남을 수도 있다. 이런 경우 기존 연결에서 다음 요청을 이어가기보다 연결을 닫고 새 연결에서 상태를 확인해야 한다. 이벤트 대기 타임아웃은 메시지 재전송을 요청하는 동작이 아니다.

프레임 제한은 사용자가 저장할 문자열 길이와 완전히 같지 않다. 명령명, 키, 인용과 이스케이프, 응답 형식까지 본문 크기에 포함된다. 메모리 제한 또한 IPC 큐와 프로세스 전체 메모리를 제한하지 않는다.

## 테스트가 확인하는 동작

테스트는 다음 파일에 나뉘어 있다.

| 파일 | 주요 검증 |
| --- | --- |
| [test_structures.py](../tests/test_structures.py) | 리스트 소속과 연결, 해시 충돌 및 확장, 힙 갱신과 임의 삭제 |
| [test_store.py](../tests/test_store.py) | UTF-8 크기, LRU, OOM 상태 보존, TTL과 인덱스 정리 |
| [test_commands.py](../tests/test_commands.py) | 인용 및 오류, 명령 출력, 구독 중복, FIFO와 느린 구독자 제한 |
| [test_protocol.py](../tests/test_protocol.py) | 분할 및 결합 수신, UTF-8, 잘못된 프레임 |
| [test_daemon.py](../tests/test_daemon.py) | 공유 상태, 연결 정리, 큰 응답, 유휴 만료, 종료, 중복 서버 거부 |
| [test_cli.py](../tests/test_cli.py) | 명시적 시작과 동시 시작, 데몬 없는 연결, 여러 서버 분리, 구독 출력, REPL 입력 보존, 남은 소켓 재시작 |
| [test_client.py](../tests/test_client.py) | 연결 시 서버 생성 금지, 반환 타입, 잘못된 응답, 이벤트 분리, 타임아웃 복구, 다른 프로세스의 push |

```sh
python3 -m unittest discover -s tests -v
```

Store 테스트는 주입한 시계로 만료 경계를 검증하고, 데몬과 CLI 및 Client 통합 테스트는 실제 Unix socket과 프로세스를 사용한다. REPL 테스트는 가상 터미널을 사용해 입력 중 이벤트 출력과 터미널 설정 복구를 확인한다.

## 기능을 확장할 때 수정할 위치

새 저장 명령을 추가한다면 Store의 상태 처리, CommandProcessor의 인자 검증과 응답, Client의 메서드와 응답 파서를 연결한다. 명령행과 REPL은 공통 전송 API를 사용하므로 일반 명령마다 별도 분기를 추가할 필요는 없다.

구독 취소처럼 연결 상태를 바꾸는 기능은 PubSub와 CommandProcessor에 구현하고 Client 메서드와 응답 변환을 추가한다. 저장소 변경과 구독 변경은 서로 다른 상태 공간에 속하므로 같은 이름의 키나 채널을 연결해 처리하지 않는다.

RESP 호환은 전송 및 응답 형식 자체의 변경이다. TCP 지원은 서버 바인딩, Client 연결과 실행 설정의 변경이다. 영속화는 Store 상태의 저장과 복구 및 만료 시각의 보존 정책이 필요하다. 어느 경우든 소켓 주소나 Client 메서드 이름 하나만 바꾸면 구현이 끝나는 기능은 아니다.
