# Mini Redis

Python 3.8+. LRU와 TTL을 지원하는 CLI 인메모리 저장소.

## 실행

```sh
python3 main.py
```

`mini-redis>` 프롬프트에서 명령을 입력하고 `exit`/`quit`(또는 Ctrl+D)로 종료합니다.
데이터는 REPL 종료 시 사라집니다.

## 테스트

```sh
python3 -m unittest discover -s tests -v
```
