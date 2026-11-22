"""会話内操作パネルから、既存の家族単位費用計算を呼ぶ。"""

import argparse
import json
import pathlib
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from agent_toolkit._common import session_cost_records, session_record_paths
from agent_toolkit._common.session_family import _load_records_checked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--self-only", action="store_true")
    args = parser.parse_args(argv)
    found = session_record_paths.find_session_record(args.session_id)
    if found is None or len(found.paths) != 1:
        raise ValueError("費用計算の原本記録を一意に解決できない")
    loaded = _load_records_checked(str(found.paths[0]))
    if loaded.records is None:
        raise ValueError(loaded.failure.reason if loaded.failure else "費用計算の原本記録を読み取れない")
    print(
        json.dumps(
            session_cost_records.estimate_detail_cost(
                found.engine,
                found.paths[0],
                [record.entry for record in loaded.records],
                include_descendants=not args.self_only,
                broken_lines=len(loaded.recoveries),
            ),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
