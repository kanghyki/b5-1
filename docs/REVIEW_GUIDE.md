# 과제 요구사항과 코드리뷰 안내

과제의 요구사항이 코드 어디에 구현되어 있는지, 코드리뷰에서 어떤 순서로 설명할지, 어떤 질문이 나올 수 있는지를 정리한다. 함수별 상세 설명은 [코드 해설](CODE_GUIDE.md)에 있다.

보너스 과제는 구현 범위에서 제외했다.

## 1. 요구사항 대응표

### 자료구조

| 요구사항 | 구현 위치 | 확인 테스트 |
| --- | --- | --- |
| 이중 연결 리스트 노드 `prev`, `next`, `data` | [Node](../mini_redis/structures/linked_list.py#L4) | |
| `insert_front`, `insert_back`, `remove_front`, `remove_back`, `remove_node`, `move_to_front` 모두 O(1) | [DoublyLinkedList](../mini_redis/structures/linked_list.py#L11) | `test_node_moves_and_removals_preserve_links` |
| 해시맵 `put`, `get`, `remove`, `contains`, `keys`, `size` | [HashMap](../mini_redis/structures/hash_map.py#L11) | `test_collisions_updates_and_growth` |
| 해시 함수 직접 설계 | [hash_key](../mini_redis/structures/hash_map.py#L25) 64비트 FNV-1a | |
| 체이닝, 이중 연결 리스트 재사용 | [_find](../mini_redis/structures/hash_map.py#L32), [_insert](../mini_redis/structures/hash_map.py#L47) | `test_collisions_updates_and_growth` |
| 로드 팩터 0.75 초과 시 버킷 2배 | [put](../mini_redis/structures/hash_map.py#L60) `size * 4 > capacity * 3` | `test_resize_threshold_and_unicode_keys` |
| 최소 힙 `push`, `pop`, `peek`, `size`, `_heapify_up`, `_heapify_down` | [MinHeap](../mini_redis/structures/min_heap.py#L4) | `test_random_pushes_pop_in_order_and_grow` |
| `(expire_at, key)` 요소 | [Store.expire](../mini_redis/store.py#L108)가 튜플을 push | `test_expire_at_ties_are_ordered_by_key` |

`size`는 메서드가 아니라 속성(`heap.size`, `mapping.size`)으로 제공한다.

### 명령어

| 명령 | 출력 | 구현 | 확인 테스트 |
| --- | --- | --- | --- |
| 공통: 키 명령 전 만료 확인 | 만료된 키는 없는 키로 처리 | 모든 Store 공개 메서드가 시작할 때 호출하는 [purge_expired](../mini_redis/store.py#L39) | `test_expiry_is_removed_from_all_structures_without_eviction_count` |
| `SET key value` | `OK` | [Store.set](../mini_redis/store.py#L49) | `test_subject_memory_and_lru_example` |
| SET 덮어쓰기 시 TTL 초기화 | | `entry.expire_at = None` | `test_set_and_delete_clear_ttl_before_recreating_key` |
| `GET key` | `"value"` / `(nil)` | [Store.get](../mini_redis/store.py#L69), 성공 시에만 LRU 갱신 | `test_get_refreshes_lru_but_exists_and_ttl_do_not` |
| `DEL key` | `(integer) 1` / `0` | [Store.delete](../mini_redis/store.py#L77) → `_delete` | `test_set_and_delete_clear_ttl_before_recreating_key` |
| `EXISTS key` | `(integer) 1` / `0` | [Store.exists](../mini_redis/store.py#L85) | `test_required_outputs_and_case_insensitive_commands` |
| `DBSIZE` | `(integer) N` | [Store.dbsize](../mini_redis/store.py#L89) | 같음 |
| `KEYS` | `1. "key"` … / `(empty array)` | [Store.keys](../mini_redis/store.py#L93) | 같음 |
| `CONFIG SET maxmemory bytes` | `OK`, 0은 무제한 | [configure_maxmemory](../mini_redis/store.py#L97), [parse_integer](../mini_redis/commands.py#L39) 최솟값 0 | `test_errors_are_standard_and_do_not_mutate_state` |
| `INFO memory` | `used_memory`, `maxmemory`, `evicted_keys` 세 줄 | [memory_info](../mini_redis/store.py#L104) | `test_required_outputs_and_case_insensitive_commands` |
| LRU 자동 제거 | SET 후 초과 시 LRU 뒤부터 제거, `evicted_keys` 증가 | [Store.set](../mini_redis/store.py#L65) | `test_lowering_limit_evicts_multiple_keys_on_next_set` |
| 단일 엔트리가 maxmemory 초과 | `(error) OOM …`, 저장하지 않음 | [Store.set](../mini_redis/store.py#L52) | `test_oom_preserves_existing_value_ttl_and_lru` |
| `EXPIRE key seconds` | 없는 키 `0`, 0 이하는 즉시 삭제 후 `1`, 정상 `1` | [Store.expire](../mini_redis/store.py#L108) | `test_nonpositive_expiry_and_eviction_clear_heap` |
| `TTL key` | 없는 키 `-2`, TTL 없음 `-1`, 남은 초 | [Store.ttl](../mini_redis/store.py#L120) | `test_expiry_is_removed_from_all_structures_without_eviction_count` |

### 에러, CLI, 입력

| 요구사항 | 구현 위치 |
| --- | --- |
| `(error) ERR unknown command '<cmd>'` | [_execute](../mini_redis/commands.py#L75) 마지막 줄 |
| `(error) ERR wrong number of arguments for '<cmd>' command` | [_arity](../mini_redis/commands.py#L57) |
| `(error) ERR value is not an integer or out of range` | [parse_integer](../mini_redis/commands.py#L39) |
| `(error) OOM command not allowed when used_memory > 'maxmemory'` | [execute](../mini_redis/commands.py#L63)가 `OutOfMemoryError`를 변환 |
| `mini-redis>` 프롬프트, `exit`/`quit` 종료 | [repl.run](../mini_redis/repl.py#L6) |
| 공백 없는 값과 큰따옴표로 감싼 값 | [tokenize](../mini_redis/commands.py#L14) |

### 제약 사항

| 제약 | 지키는 방법 |
| --- | --- |
| `dict`, `set`, `collections` 금지 | 프로젝트 코드 전체에서 사용하지 않는다. Python `list`는 해시맵 버킷 배열과 힙 저장소(고정 길이로 만들고 직접 2배 확장), 입력 토큰과 KEYS 출력 줄을 모으는 데만 쓴다. |
| 자료구조마다 독립 파일 | `structures/linked_list.py`, `hash_map.py`, `min_heap.py` |
| 핵심 클래스·함수 docstring | 모든 모듈, `HashMap`·`DoublyLinkedList`·`MinHeap`·`Store`·`CommandProcessor`, `hash_key`·`tokenize`·`purge_expired`·`run` 등 핵심 함수에 docstring |
| 네트워크, 영속성, 복잡 자료형, 동시성 없음 | 단일 프로세스 REPL, 메모리에만 저장, String 명령만 |
| Python 3.8 이상 | 3.9 이후 문법·API를 쓰지 않는다. 외부 패키지가 없다. |

## 2. 코드리뷰 설명 흐름

전체 15분 정도를 기준으로 한다. 아래 순서로 설명하면 자료구조를 먼저 이해한 뒤 그것들을 묶는 Store를 볼 수 있다.

### 0단계. 데모 (2분)

과제 예시를 그대로 입력해 동작을 먼저 보여준다.

```text
mini-redis> CONFIG SET maxmemory 30
mini-redis> SET user:1 "Alice"
mini-redis> SET user:2 "Bob"
mini-redis> SET user:3 "Charlie"
mini-redis> GET user:1
mini-redis> INFO memory
mini-redis> KEYS
mini-redis> EXPIRE user:2 3
mini-redis> TTL user:2
(3초 대기)
mini-redis> GET user:2
mini-redis> TTL user:2
mini-redis> GET
mini-redis> HELLO
mini-redis> exit
```

`user:1`이 제거되는 이유를 계산으로 보여준다. `user:1`+`Alice` = 11, `user:2`+`Bob` = 9, `user:3`+`Charlie` = 13바이트다. 세 번째 SET 후 합계 33이 30을 넘으므로 가장 오래 쓰지 않은 `user:1`(11바이트)을 제거해 22가 된다.

### 1단계. 전체 구조 (1분)

```text
main.py → repl.run() → CommandProcessor.execute() → Store
                                                     ├─ HashMap          key → Entry
                                                     ├─ DoublyLinkedList LRU 순서
                                                     └─ MinHeap          (expire_at, key)
```

- 단일 프로세스 REPL이다. 보너스(Pub/Sub)는 제외했다.
- 층마다 책임이 하나다. REPL은 입출력, CommandProcessor는 파싱과 출력 형식, Store는 상태, structures는 자료구조를 맡는다.

### 2단계. 이중 연결 리스트 (2분)

보여줄 곳: [linked_list.py](../mini_redis/structures/linked_list.py)

- head·tail 센티널이 있어 빈 리스트와 양 끝에서도 분기 없이 `_insert_between` 하나로 삽입한다.
- `remove_node`는 노드 참조만 있으면 양 이웃을 바로 잇는다. 이전 노드를 찾을 필요가 없으므로 O(1)이다.
- `move_to_front`는 끊고 앞에 다시 붙이는 것이라 노드 객체가 그대로 유지된다. 그래서 Entry가 가진 `lru_node` 참조가 계속 유효하다.

### 3단계. 해시맵 (3분)

보여줄 곳: [hash_map.py](../mini_redis/structures/hash_map.py)

- `hash_key`: 문자열을 UTF-8 바이트로 바꾸고 바이트마다 XOR 후 FNV 소수를 곱한다(FNV-1a). `% capacity`로 버킷 인덱스를 구한다.
- `_find`: 버킷의 연결 리스트를 따라가며 실제 키를 비교한다. 해시가 같아도 다른 키를 돌려주지 않는다.
- `put`: 새 키를 넣은 뒤 `size * 4 > capacity * 3`이면 버킷을 두 배로 만들고 전체를 다시 넣는다. 0.75를 정수 비교로 표현한 것이다.

### 4단계. 최소 힙 (2분)

보여줄 곳: [min_heap.py](../mini_redis/structures/min_heap.py)

- 배열로 완전 이진 트리를 표현한다. 부모 `(i - 1) // 2`, 자식 `2i + 1`, `2i + 2`.
- `push`는 끝에 넣고 `_heapify_up`, `pop`은 마지막 요소를 루트로 옮기고 `_heapify_down`.
- 요소는 `(expire_at, key)` 튜플이다. 튜플 비교로 시각이 같으면 key 순서가 된다.

### 5단계. Store: 세 자료구조 묶기 (4분)

보여줄 곳: [store.py](../mini_redis/store.py). 설명의 중심이다.

1. **Entry**: `key`, `value`, `byte_size`, `lru_node`, `expire_at`. 해시맵이 Entry를 찾으면 LRU 노드까지 바로 닿는다. 이것이 O(1) LRU의 핵심이다.
2. **`set()` 흐름**: 만료 정리 → 크기 계산 → OOM 검사(상태 변경 전) → 새 키 삽입 또는 덮어쓰기(TTL 초기화, LRU 앞으로) → `used_memory` 갱신 → 초과하면 `peek_back()`부터 제거하고 `evicted_keys` 증가.
3. **`get()`**: 찾았을 때만 `move_to_front`. EXISTS, TTL은 LRU를 건드리지 않는다.
4. **`_delete()`**: DEL, 만료, LRU 제거가 모두 이 함수 하나를 거친다. 해시맵, LRU, `used_memory`가 항상 같이 갱신된다.
5. **TTL lazy deletion**: EXPIRE는 튜플을 push만 한다. `purge_expired()`는 시각이 지난 튜플을 꺼내 `entry.expire_at`과 같을 때만 지운다. 아래 예시로 설명한다.

```text
EXPIRE k 10  → 힙 [(10,k)]          expire_at=10
EXPIRE k 30  → 힙 [(10,k), (30,k)]  expire_at=30
t=10 purge   → (10,k) 꺼냄, 10≠30 → 버림
t=30 purge   → (30,k) 꺼냄, 30=30 → 삭제
```

### 6단계. 명령 처리와 REPL (1분)

보여줄 곳: [commands.py](../mini_redis/commands.py), [repl.py](../mini_redis/repl.py)

- `tokenize`: 큰따옴표 안의 공백은 값에 포함한다. 닫히지 않은 따옴표는 에러다.
- `_execute`: 인자 수와 정수 검증을 Store 호출 전에 끝낸다. 그래서 에러가 난 명령은 상태를 바꾸지 않는다.
- REPL은 `input()` 루프다. 입력 함수를 인자로 받아 테스트에서 표준 입력 없이 실행한다.

### 7단계. 테스트 (1분)

```sh
python3 -m unittest discover -s tests -v
```

- Store 테스트는 시계를 주입해 실제로 기다리지 않고 만료를 검증한다.
- Store 테스트는 모두 끝에서 `assert_consistent()`로 해시맵·LRU 크기, `used_memory` 합계, TTL이 있는 키마다 힙 항목이 있는지 확인한다.

## 3. 예상 질문과 답

### 해시맵

**Q. 왜 Python 내장 `hash()`를 쓰지 않았나?**
과제가 해시 함수를 직접 설계하라고 요구한다. 또 문자열 `hash()`는 프로세스마다 `PYTHONHASHSEED`로 값이 달라져서 결과를 재현하기 어렵다. FNV-1a는 바이트마다 XOR와 곱셈만 하므로 구현이 짧고 분포가 고르다.

**Q. 충돌이 나면 어떻게 되나?**
같은 버킷의 연결 리스트에 이어 붙이고, 찾을 때 실제 키를 비교한다. 최악의 경우(모든 키가 한 버킷) 조회가 O(N)이 된다. 테스트는 해시를 항상 0으로 돌려주는 `CollisionMap`으로 이 경우를 확인한다.

**Q. 왜 0.75인가? 확장 비용은?**
버킷 공간과 체인 길이 사이의 절충이며 Java `HashMap`의 기본값과 같다. 확장 한 번은 전체 재배치라 O(N)이지만, 두 배씩 늘리므로 삽입 한 번당 분할 상환 비용은 O(1)이다.

**Q. 삭제하면 버킷을 줄이나?**
줄이지 않는다. 과제 요구에 없다.

**Q. KEYS 결과가 입력 순서가 아닌 이유는?**
버킷 배열 순서로 순회하기 때문이다. 과제는 KEYS의 순서를 요구하지 않는다.

**Q. `size`가 메서드 목록에 있는데 속성인 이유는?**
값을 읽기만 하므로 속성으로 두었다. `size()`로 바꿔도 동작은 같다.

### LRU와 메모리

**Q. 해시맵과 이중 연결 리스트를 조합하면 왜 O(1)인가?**
해시맵이 키로 Entry를 평균 O(1)에 찾고, Entry가 LRU 노드 참조를 갖고 있다. 이중 연결 리스트는 노드 참조만 있으면 앞 노드를 찾지 않고 끊을 수 있다. 단일 연결 리스트라면 앞 노드를 찾느라 O(N)이 든다.

**Q. 어떤 명령이 LRU를 갱신하나?**
SET과 성공한 GET뿐이다. EXISTS, TTL, KEYS는 갱신하지 않는다. 만료로 지워진 키의 GET도 갱신하지 않는다.

**Q. maxmemory를 현재 사용량보다 낮추면 바로 제거하나?**
아니다. 과제가 "SET 이후"에 제거한다고 명시하므로 다음 SET에서 제거한다.

**Q. 덮어쓴 키가 자기 자신을 제거할 수 있나?**
없다. 덮어쓴 키는 먼저 LRU 맨 앞으로 옮기므로 다른 키부터 제거된다. 그 키만 남는 경우에도 OOM 검사로 크기가 maxmemory 이하임이 이미 보장되어 반복이 끝난다.

**Q. OOM이면 기존 값은?**
크기 검사를 상태 변경 전에 하므로 기존 값, TTL, LRU 위치가 모두 그대로다.

**Q. `used_memory`는 어떻게 계산하나?**
키와 값의 UTF-8 바이트 수 합이다. 한글 한 글자는 3바이트다. 덮어쓰면 이전 크기를 빼고 새 크기를 더한다. 노드, 버킷, 힙 같은 자료구조 오버헤드는 과제 기준대로 제외한다.

**Q. 만료된 키도 `evicted_keys`에 세나?**
세지 않는다. `evicted_keys`는 메모리 초과로 제거한 키만 센다.

### TTL과 힙

**Q. 왜 TTL 관리에 힙을 쓰나?**
알아야 할 것은 "가장 빨리 만료되는 키"뿐이다. 힙은 그것을 O(1)에 보고 O(log n)에 넣고 뺀다. 정렬 리스트는 삽입이 O(n)이고, 매번 전체를 훑으면 O(n)이다.

**Q. 만료는 언제 일어나나?**
백그라운드 타이머는 없다. Store의 모든 공개 메서드가 먼저 `purge_expired()`로 시각이 지난 키를 한꺼번에 지운다. 입력을 기다리는 동안 만료된 키도 다음 명령을 실행하기 전에 지워지므로 결과는 같다.

**Q. 과제는 DEL이 TTL 구조에서도 엔트리를 제거하라고 하는데, 힙에 튜플이 남지 않나?**
Entry가 삭제되므로 그 키의 TTL 정보는 사라진다. 힙에 남은 튜플은 나중에 꺼낼 때 Entry가 없거나 `expire_at`이 달라서 무시된다. 과제가 힙 구현에서 lazy deletion을 허용한다고 명시한다. 대가로 낡은 튜플이 원래 만료 시각까지 힙 공간을 차지한다.

**Q. 낡은 튜플은 어떻게 구분하나?**
꺼낸 시각이 `entry.expire_at`과 같을 때만 유효하다. EXPIRE 재설정, SET 덮어쓰기, DEL·LRU 제거 후 재생성 세 경우 모두 이 비교로 걸러진다.

**Q. float를 `==`로 비교해도 되나?**
같은 계산 결과를 Entry와 튜플에 그대로 넣었으므로 오차가 생기지 않는다. 새로 계산한 값끼리 비교하는 것이 아니다.

**Q. `EXPIRE user:2 3` 직후 TTL이 3이 아니라 2인 이유는?**
남은 시간 2.99…초를 `int()`로 버리기 때문이다. 과제 예시 출력과 같다.

**Q. 왜 `time.monotonic()`인가?**
시스템 시각이 바뀌어도 거꾸로 가지 않는다. 생성자로 시계를 주입할 수 있어 테스트에서 시간을 직접 진행시킨다.

### 입력과 에러

**Q. 따옴표 입력은 어디까지 지원하나?**
공백 없는 값과 큰따옴표로 감싼 값을 모두 지원한다. `""`는 빈 문자열이다. 이스케이프는 지원하지 않고, 닫히지 않은 따옴표는 에러다.

**Q. 잘못된 명령이 상태를 바꾸지 않는다는 근거는?**
인자 수와 정수 검증이 Store 호출 전에 끝난다. OOM도 Store 안에서 상태를 바꾸기 전에 검사한다. `test_errors_are_standard_and_do_not_mutate_state`가 확인한다.

**Q. 정수 검증을 왜 직접 하나? `int()`면 되지 않나?**
`int()`는 전각 숫자(`１２`)와 `1_000`을 받아들인다. 아주 큰 수는 `EXPIRE`에서 `clock() + seconds` 계산 중 `OverflowError`를 일으켜 REPL을 종료시킨다. 그래서 ASCII 숫자와 signed 64-bit 범위만 허용한다.

**Q. 명령 대소문자는?**
명령 이름은 구분하지 않고, 키와 값은 구분한다.

### 범위와 제약

**Q. `list`는 써도 되나?**
과제가 금지한 것은 `dict`, `set`, `collections`와 내장 컬렉션으로 해시맵이나 캐시를 대체하는 것이다. `list`는 버킷 배열과 힙 저장소처럼 인덱스로 접근하는 배열로만 쓴다. 크기 확장도 직접 새 배열을 만들어 복사한다. 그 밖에는 입력 토큰과 출력 줄을 모으는 데만 쓴다.

**Q. 보너스 과제는?**
구현하지 않았다. Pub/Sub을 위해 데몬과 Unix 소켓 구조를 만들었었지만, 과제의 "네트워크 통신은 구현하지 않는다(오직 CLI)" 제약과 구조 단순화를 위해 제거했다.
