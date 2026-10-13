#!/bin/bash
# .cmdファイルがCP932でエンコードされているか検証する。
set -eu

dir="${1:-.}"
errors=0

while IFS= read -r -d '' file; do
    if ! uv run --frozen python -c "
import sys
sys.stdout.reconfigure(encoding='utf-8')

with open(sys.argv[1], 'rb') as f:
    try:
        f.read().decode('cp932')
    except UnicodeDecodeError as e:
        print(f'{sys.argv[1]}: {e}。次の操作: このファイルをCP932で保存し直し、scripts/check-cmd-encoding.shを再実行してください。')
        sys.exit(1)
" "$file"; then
        errors=$((errors + 1))
    fi
done < <(find "$dir" -name '*.cmd' -print0)

if [ "$errors" -gt 0 ]; then
    echo "ERROR: ${errors} file(s) with invalid CP932 encoding。次の操作: 上記ファイルをCP932で保存し直し、scripts/check-cmd-encoding.shを再実行してください。"
    exit 1
fi
