# Mini Redis

Python 3.8+, Linux/macOS. LRU, TTL, Pub/Sub을 지원하는 CLI 인메모리 저장소.

## 실행

```sh
python3 main.py                      # REPL, 데몬 자동 시작
python3 main.py SET user:1 "Alice"    # 단일 명령
python3 main.py GET user:1
python3 main.py SUBSCRIBE news       # 메시지 수신, Ctrl+C로 종료
python3 main.py PUBLISH news "hello" # 다른 터미널에서 실행
python3 main.py daemon status
python3 main.py daemon stop
```

REPL은 `exit`/`quit`으로 종료합니다. 데이터는 데몬 종료 시 사라집니다.
`--runtime-dir PATH`로 별도 인스턴스를 지정할 수 있습니다.

## 테스트

```sh
python3 -m unittest discover -s tests -v
```
