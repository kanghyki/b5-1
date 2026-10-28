# mini_redis 코드 해설

mini_redis는 단일 프로세스 REPL이다. 입력 한 줄은 명령 처리 계층을 거쳐 직접 구현한 자료구조 기반 저장소에 도달하고, Redis 스타일 문자열로 출력된다. 네트워크 통신과 영속화는 없으며 데이터는 REPL 종료 시 사라진다.

## 코드 탐색 순서

```text
main.py
  → repl.run()                     input("mini-redis> ") 반복
    → CommandProcessor.execute()   토큰화, 인자 검증, 출력 문자열 생성
      → Store                      HashMap + DoublyLinkedList(LRU) + MinHeap(TTL)
    ← "OK", "(nil)", "(integer) N", "(error) ..."
```

자료구조를 따로 이해하려면 [linked_list.py](../mini_redis/structures/linked_list.py), [hash_map.py](../mini_redis/structures/hash_map.py), [min_heap.py](../mini_redis/structures/min_heap.py), [store.py](../mini_redis/store.py) 순서로 읽는다.

## main.py와 repl.py

[main.py](../main.py)는 `mini_redis.repl.run()`의 반환값을 프로세스 종료 코드로 전달한다.

[run()](../mini_redis/repl.py)은 `mini-redis> ` 프롬프트로 한 줄을 읽어 `CommandProcessor.execute()`에 넘기고, 결과가 비어 있지 않으면 출력한다. `exit`/`quit`, EOF(Ctrl+D), Ctrl+C에서 0을 반환한다. 명령 오류는 출력만 하고 다음 입력을 받는다. 입력 함수 `read`를 인자로 받으므로 테스트에서 표준 입력 없이 실행할 수 있다.

## commands.py

### 인용과 토큰화

[quote(value)](../mini_redis/commands.py)는 문자열을 큰따옴표로 감싸 `GET`과 `KEYS` 출력에 사용한다.

`tokenize(line)`은 문자열을 순회하며 인용 여부와 토큰 시작 여부를 관리한다. 인용 밖의 공백은 구분자이고, 큰따옴표는 인용 상태를 바꾼다. 따라서 `"hello world"`는 공백을 포함한 하나의 값이 된다. 이스케이프는 지원하지 않으며 작은따옴표는 일반 문자다.

`started`는 빈 문자열 `""`도 토큰으로 보존하기 위해 필요하다. 큰따옴표가 닫히지 않으면 `CommandError`다.

`parse_integer()`는 선택적인 부호와 ASCII 숫자만 허용하고 signed 64-bit 범위를 검사한다. `CONFIG SET maxmemory`는 최솟값 0을 추가 적용한다. 정수처럼 보이는 실수나 범위를 초과한 값은 상태 변경 전에 거부한다.

### 명령 실행과 표시 형식

`CommandProcessor.execute()`는 토큰을 만든 후 `_execute()`로 전달한다. 빈 입력은 빈 문자열이다. `_execute()`는 명령명을 대문자로 바꿔 비교하므로 명령명은 대소문자를 구분하지 않지만 키와 값은 구분한다. `_arity()`로 정확한 인자 수를 검사한다.

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

명령 오류는 `(error) ...` 문자열, 저장 불가능한 단일 항목은 OOM 오류 문자열로 변환한다. 검증이 모두 끝난 뒤 Store를 호출하므로 오류가 난 명령은 상태를 바꾸지 않는다.

## store.py

### Entry와 세 인덱스

[Entry](../mini_redis/store.py)는 `key`, `value`, `byte_size`, `lru_node`, `expire_at`을 가진다. `lru_node`는 LRU 리스트 노드의 직접 참조이고, `expire_at`은 현재 만료 시각 숫자이거나 TTL이 없으면 `None`이다.

Store의 `_entries`는 키에서 Entry를 찾고, `_lru`는 앞에서 뒤로 최근 접근 순서를 보관하며, `_expiry`는 `(expire_at, key)` 튜플을 만료 시각 순서로 보관한다. `_delete(entry)`는 해시맵, LRU, 메모리 합계를 함께 갱신한다. 삭제와 만료, 메모리 제거가 이 함수를 공유하므로 정리 누락을 줄인다. 힙에 남은 항목은 지우지 않고 나중에 꺼낼 때 걸러낸다(lazy deletion).

### set과 get

`set()`은 다음 순서로 처리한다.

1. 이미 만료된 키를 정리한다.
2. 키와 값의 UTF-8 길이로 새 항목 크기를 계산한다.
3. 항목 하나가 양수 maxmemory보다 크면 OOM으로 끝낸다.
4. 새 키는 Entry를 만들고 LRU 앞과 해시맵에 삽입한다.
5. 기존 키는 `expire_at`을 `None`으로 바꿔 TTL을 제거하고 이전 바이트 수를 빼며 값과 크기를 갱신하고 LRU 앞으로 이동한다.
6. 새 바이트 수를 더한다.
7. 전체 크기가 제한을 넘으면 LRU 뒤부터 제거하고 `evicted_keys`를 증가시킨다.

OOM 검사를 먼저 하므로 기존 키를 너무 큰 값으로 덮어쓰는 실패가 기존 값, TTL, 접근 순서를 변경하지 않는다. 정상적인 덮어쓰기는 TTL을 제거한다. `get()`은 만료 정리 후 Entry가 있으면 LRU 앞으로 옮기고 값을 반환하며, 없으면 `None`이다.

예를 들어 LRU가 `[c, b, a]`일 때 `GET a`는 `[a, c, b]`를 만든다. 이후 새 키 `d`를 저장해 한 키를 제거해야 하면 `b`가 제거된다. `EXISTS b`만 호출했다면 순서는 유지된다.

### 메모리 통계와 단순 조회

`delete()`는 키를 찾아 공통 삭제 후 1을 반환하고, 없으면 0이다. `exists()`는 정수 0 또는 1, `dbsize()`는 해시맵 크기, `keys()`는 키 generator를 반환한다. CommandProcessor가 generator를 소비해 표시 문자열을 만든다.

`configure_maxmemory()`는 음수를 거부하고 제한을 저장한다. 기존 키의 LRU 제거는 다음 성공한 `set()`에서 수행한다. `memory_info()`는 `(used_memory, maxmemory, evicted_keys)` 튜플을 반환한다. 이 메서드들도 호출 시 만료된 키를 정리한다.

### 만료 처리

`expire()`는 키가 없으면 0, 0 이하 시간은 즉시 삭제 후 1을 반환한다. 양수 시간은 `_clock() + seconds`를 `entry.expire_at`에 저장하고 `(expire_at, key)`를 힙에 넣는다. 기존 TTL이 있어도 새 항목을 하나 더 넣을 뿐 이전 항목은 그대로 둔다.

`purge_expired()`는 현재 시각을 한 번 읽고, 힙 최솟값의 시각이 그 이하인 동안 항목을 꺼낸다. 꺼낸 key로 Entry를 찾아 `entry.expire_at`이 꺼낸 시각과 같을 때만 `_delete()`한다. 다르면 낡은 항목이므로 버린다. 낡은 항목은 다음 세 경우에 생긴다.

| 경우 | 꺼낼 때 상태 |
| --- | --- |
| 같은 키에 EXPIRE를 다시 호출 | `expire_at`이 새 시각이라 다름 |
| SET으로 덮어씀 | `expire_at`이 `None` |
| DEL, LRU 제거, 다른 만료로 키가 사라짐 | Entry가 없거나 다시 만든 Entry의 `expire_at`이 다름 |

낡은 항목은 원래 만료 시각까지 힙에 남는다. 자료구조 오버헤드이므로 `used_memory`에는 포함되지 않는다.

`ttl()`은 만료 정리 후 키 없음 -2, TTL 없음 -1을 반환한다. TTL이 있으면 남은 초를 정수로 버림하고 0 이상으로 제한한다. 시계는 생성자로 주입 가능하므로 테스트에서는 실제 대기 없이 시각을 진행시킬 수 있다.

## 직접 구현한 자료구조

### DoublyLinkedList

[이중 연결 리스트](../mini_redis/structures/linked_list.py)는 head와 tail 센티널 사이에 데이터 노드를 둔다. 센티널은 실제 데이터가 아닌 경계 노드이며 빈 리스트도 head와 tail이 연결되어 있다.

```text
head ⇄ 최근 접근 Entry ⇄ ... ⇄ 오래된 Entry ⇄ tail
```

`remove_node()`는 양쪽 이웃을 연결한 뒤 제거된 노드의 참조를 비운다. 노드가 이 리스트 소속인지는 검사하지 않으므로 호출하는 쪽이 자기 리스트의 데이터 노드만 넘긴다. Store는 `_lru`에서 만든 `entry.lru_node`만, HashMap은 같은 버킷에서 찾은 노드만 넘긴다. `move_to_front()`는 기존 노드를 제거했다가 앞에 다시 연결하므로 Entry의 노드 참조가 유지된다.

앞뒤 삽입, 앞뒤 조회 및 제거, 알고 있는 노드의 제거와 이동은 O(1)이다. 노드를 값으로 검색하는 기능은 없다. `nodes()`는 yield 전에 다음 노드를 기억해 순회 중 현재 노드 삭제를 지원한다. 임의의 다른 노드를 삭제하는 모든 순회 변경까지 보장하는 것은 아니다.

이 리스트는 LRU 외에 해시 충돌 체인에도 재사용한다.

### HashMap

[해시맵](../mini_redis/structures/hash_map.py)은 문자열 키를 UTF-8로 인코딩하고 64비트 FNV-1a 해시를 계산한다. 각 바이트에 XOR와 곱셈을 적용하고 64비트로 제한한 뒤 버킷 수로 나눈 나머지를 인덱스로 사용한다. Python 내장 `hash()`에 의존하지 않는다.

같은 버킷에 들어간 Pair들은 DoublyLinkedList로 연결한다. `_find()`는 해당 버킷에서 실제 키를 비교하므로 해시 충돌이 나도 다른 키의 값을 반환하지 않는다. `contains()`는 값이 `None`인 경우도 존재 여부를 구별할 수 있다.

`put()`은 기존 키의 값만 바꾸거나 새 Pair를 삽입한다. `size * 4 > capacity * 3`, 즉 부하율 0.75 초과 시 버킷을 두 배로 만들고 전체 Pair를 다시 배치한다. 확장 시 Pair는 재사용하되 버킷 연결 노드는 새로 만든다. 기존 키 갱신은 size를 늘리지 않으며 삭제 시 버킷을 줄이지 않는다.

문자열 길이를 별도로 고려하면 해시 계산 자체가 UTF-8 바이트 수에 비례한다. 충돌이 적을 때 조회는 평균 O(1)이지만 한 버킷에 키가 몰리면 O(N)이고 확장은 일시에 전체 항목을 재배치한다. 실제 Redis의 내부 해시 구현이나 확장 전략을 그대로 옮긴 것은 아니다.

### MinHeap

[최소 힙](../mini_redis/structures/min_heap.py)은 배열의 앞 `size`개 위치를 사용한다. 부모 인덱스는 `(i - 1) // 2`, 자식은 `2i + 1`, `2i + 2`다. 부모가 자식 이하라는 관계를 유지해 배열 맨 앞에서 가장 빠른 만료 항목을 찾는다. 요소는 `(expire_at, key)` 튜플이고 Python 튜플 비교를 그대로 사용하므로, 시각이 같으면 key 순서로 꺼낸다.

`push()`는 끝에 넣고 `_heapify_up()`으로 올리며 공간이 모자라면 두 배 배열을 직접 만들고 복사한다. `pop()`은 맨 앞을 꺼내고 마지막 요소로 빈자리를 메운 뒤 `_heapify_down()`으로 내린다. `peek()`은 O(1), `push()`와 `pop()`은 O(log H)이며 배열 확장은 별도 O(H) 비용이 있다. H는 낡은 항목을 포함한 힙 크기다.

임의 항목 삭제나 시각 갱신은 지원하지 않는다. 그 대신 Store가 lazy deletion으로 낡은 항목을 꺼낼 때 걸러낸다.

### 전체 명령 비용

N은 저장 키 수, H는 낡은 항목을 포함한 힙 크기, E는 이번 호출에서 정리할 만료 키 수, V는 제거할 LRU 키 수다. 아래는 해시맵 평균 성능을 가정한 주요 비용이며 문자열 인코딩과 응답 조립 비용은 별도로 발생한다.

| 작업 | 주요 비용 |
| --- | --- |
| 기본 키 조회와 LRU 이동 | 평균 O(1) |
| EXPIRE | O(log H) |
| TTL이 있는 키의 SET 덮어쓰기와 DEL | 평균 O(1), 힙은 건드리지 않음 |
| 만료 항목 정리 | 꺼내는 항목 수 E에 대해 O(E log H), 낡은 항목도 E에 포함 |
| 메모리 초과 제거 | 평균 O(V) |
| KEYS | 버킷과 항목 순회, 결과 문자열 생성 |

해시맵은 삭제 후 축소하지 않으므로 전체 순회 비용에는 현재 항목 수뿐 아니라 유지 중인 버킷 수도 영향을 준다. 이 비용과 만료 정리는 명령의 전체 실행 시간에 포함된다.

## 테스트가 확인하는 동작

| 파일 | 주요 검증 |
| --- | --- |
| [test_structures.py](../tests/test_structures.py) | 리스트 연결, 해시 충돌 및 확장, 힙 정렬 순서와 확장 |
| [test_store.py](../tests/test_store.py) | UTF-8 크기, LRU, OOM 상태 보존, TTL과 낡은 힙 항목 무시 |
| [test_commands.py](../tests/test_commands.py) | 인용, 명령 출력, 표준 오류와 상태 보존 |
| [test_repl.py](../tests/test_repl.py) | 프롬프트, 명령 실행, exit/quit과 EOF 종료 |

```sh
python3 -m unittest discover -s tests -v
```

Store 테스트는 주입한 시계로 실제 대기 없이 만료 경계를 검증한다.

## 기능을 확장할 때 수정할 위치

새 저장 명령은 Store에 상태 처리를, CommandProcessor의 `_execute()`에 인자 검증과 출력 형식을 추가한다. REPL은 모든 입력을 그대로 CommandProcessor에 넘기므로 수정할 필요가 없다.
