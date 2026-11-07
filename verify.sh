#!/bin/sh
# macOS / Linux 自检：编码、CLI 单元测试，以及一次真实行情获取。
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
ROOT=$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)

if command -v python3 >/dev/null 2>&1; then
    PY=python3
else
    PY=python
fi

echo "[1/3] 检查 watch_quotes.ps1 的 UTF-8 BOM"
BOM=$(head -c 3 "$ROOT/watch_quotes.ps1" | od -An -tx1 | tr -d ' \n')
if [ "$BOM" != "efbbbf" ]; then
    echo "[FAIL] watch_quotes.ps1 缺少 UTF-8 BOM" >&2
    exit 1
fi
echo "[PASS] watch_quotes.ps1 BOM 正常"

echo "[2/3] Python 单元测试"
"$PY" -m unittest discover -s "$ROOT/tests" -v || exit 1

echo "[3/3] 真实行情 --once"
"$PY" "$ROOT/watch_quotes.py" --once || exit 1
