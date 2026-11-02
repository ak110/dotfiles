#!/usr/bin/env python3
"""公開modから受け取ったmain会話IDを、既存の原本記録へ解決する。"""

import argparse
import json
import sys

from agent_toolkit._common.session_record_paths import find_session_record


def main(argv: list[str] | None = None) -> int:
    """一意なClaudeセッション記録だけを返し、不明な記録で圧縮を置き換えない。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args(argv)
    record = find_session_record(args.session_id)
    if record is None or record.engine != "claude" or len(record.paths) != 1:
        print("会話の原本記録を一意に解決できないため、標準の圧縮を使用する。", file=sys.stderr)
        return 2
    print(json.dumps({"record_path": str(record.paths[0].resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
