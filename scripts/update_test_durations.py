"""全3shardのJUnitから、1秒以上の試験所要記録を生成する。標準Pythonだけで動く。"""

import argparse
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path


def read_durations(paths: list[Path]) -> dict[str, float]:
    """重複・不正な試験を拒否し、pytestのnode IDへ変換する。"""
    if len(paths) != 3 or len({path.resolve() for path in paths}) != 3:
        raise ValueError("異なる全3shardのJUnit XMLを指定してください")
    durations: dict[str, float] = {}
    for path in paths:
        try:
            root = ET.parse(path).getroot()
        except OSError as error:
            raise ValueError(
                f"JUnitを読み取れません: {path}: {error}。入力パスと読取権限を確認して再実行してください"
            ) from error
        except ET.ParseError as error:
            raise ValueError(
                f"JUnit XMLが不正です: {path}: {error}。成功runの全3shardを再取得して再実行してください"
            ) from error
        cases = list(root.iter("testcase"))
        if root.tag not in {"testsuites", "testsuite"} or not cases:
            raise ValueError(f"JUnitの試験がありません: {path}")
        for case in cases:
            classname = case.get("classname", "").split(".")
            name = case.get("name", "")
            module = next((i for i, part in enumerate(classname) if part.endswith("_test")), None)
            if module is None or not name or any(not part for part in classname):
                raise ValueError(f"pytestの識別子が不正です: {path}: {case.attrib}")
            nodeid = "/".join(classname[: module + 1]) + ".py::" + "::".join([*classname[module + 1 :], name])
            try:
                duration = float(case.attrib["time"])
            except (ValueError, KeyError) as error:
                raise ValueError(f"試験所要が不正です: {nodeid}: {case.get('time')}") from error
            if not math.isfinite(duration) or duration < 0:
                raise ValueError(f"試験所要が不正です: {nodeid}: {duration}")
            if nodeid in durations:
                raise ValueError(f"試験が重複しています: {nodeid}")
            if case.find("failure") is not None or case.find("error") is not None:
                raise ValueError(f"未成功の試験です: {nodeid}")
            durations[nodeid] = duration
    return durations


def main() -> int:
    """入力を全件検査してから指定先へ記録する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("junit", nargs=3, type=Path, help="成功runの全3shardのJUnit XML")
    parser.add_argument("--output", required=True, type=Path, help="所要記録JSONの保存先")
    args = parser.parse_args()
    try:
        durations = read_durations(args.junit)
        recorded = {nodeid: value for nodeid, value in durations.items() if value >= 1.0}
    except ValueError as error:
        detail = str(error)
        if "再実行してください" not in detail:
            detail += "。同じ成功runの異なる全3shardを再取得し、識別子・所要・成功結果を確認して再実行してください"
        parser.error(detail)
    try:
        args.output.write_text(json.dumps(recorded, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        parser.error(
            f"所要記録を保存できません: {args.output}: {error}。保存先の親ディレクトリと書込権限を確認して再実行してください"
        )
    print(f"入力3shard: {len(durations)}件、保存: {len(recorded)}件（1秒以上）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
