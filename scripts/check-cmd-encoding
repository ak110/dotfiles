#!/bin/bash
# .cmdファイルがCP932でエンコードされているか検証する。
set -eu

dir="${1:-.}"
errors=0

while IFS= read -r -d '' file; do
    if ! uv run --frozen python -c "
import sys

with open(sys.argv[1], 'rb') as f:
    try:
        f.read().decode('cp932')
    except UnicodeDecodeError as e:
        print(f'{sys.argv[1]}: {e}')
        sys.exit(1)
" "$file"; then
        errors=$((errors + 1))
    fi
done < <(find "$dir" -name '*.cmd' -print0)

if [ "$errors" -gt 0 ]; then
    echo "ERROR: ${errors} file(s) with invalid CP932 encoding"
    exit 1
fi
echo "OK: all .cmd files are valid CP932"
