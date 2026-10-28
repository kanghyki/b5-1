"""mini-redis> 프롬프트로 명령을 읽고 실행 결과를 출력하는 REPL."""

from .commands import CommandProcessor


def run(processor=None, read=input):
    """exit/quit, EOF(Ctrl+D), Ctrl+C 입력 시 종료한다."""
    processor = processor if processor is not None else CommandProcessor()
    while True:
        try:
            line = read("mini-redis> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line.strip().lower() in ("exit", "quit"):
            return 0
        output = processor.execute(line)
        if output:
            print(output)
