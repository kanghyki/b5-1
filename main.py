"""Mini Redis CLI 실행 진입점."""

import sys

from mini_redis.repl import run


if __name__ == "__main__":
    sys.exit(run())
