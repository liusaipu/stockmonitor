#!/bin/sh
# 股票行情监视器 macOS / Linux 启动器
# 与 Windows 的 watch.cmd 接受完全相同的参数。
set -u

SOURCE=$0
while [ -h "$SOURCE" ]; do
    DIR=$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)
    SOURCE=$(readlink "$SOURCE")
    case $SOURCE in
        /*) ;;
        *) SOURCE=$DIR/$SOURCE ;;
    esac
done
SCRIPT_DIR=$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)

if [ -n "${WATCH_PYTHON:-}" ]; then
    PY=$WATCH_PYTHON
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "错误: 未找到 Python 3.8+。请先安装 Python，或设置 WATCH_PYTHON 指向解释器。" >&2
    exit 127
fi

if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    echo "错误: 需要 Python 3.8 或更高版本（当前: $("$PY" --version 2>&1)）。" >&2
    exit 127
fi

exec "$PY" "$SCRIPT_DIR/watch_quotes.py" "$@"
