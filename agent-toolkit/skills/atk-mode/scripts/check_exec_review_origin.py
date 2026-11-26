"""実行レビューの返却を原本の逐語引用と突き合わせて採用可否を確かめる。 pyfltr: allow-comment"""

import argparse
import pathlib
import sys

from agent_toolkit._plan import review_contract


def main() -> int:
    parser = argparse.ArgumentParser(description="実行レビューの原文要求の判定を検証する")
    parser.add_argument("result", type=pathlib.Path, help="正式な実行レビュー返却の保存ファイル")
    parser.add_argument("originals", type=pathlib.Path, nargs="+", help="全対象AWIの保存本文")
    args = parser.parse_args()
    try:
        originals = {path.name: path.read_text(encoding="utf-8") for path in args.originals}
        if len(originals) != len(args.originals):
            raise ValueError("同じWI名の原本を複数指定できない")
        review_contract.validate_origin_verdicts(args.result.read_text(encoding="utf-8"), originals)
    except (ValueError, OSError) as error:
        print(f"原文要求の判定を受理できない: {error}", file=sys.stderr)
        return 1
    print("逐語引用の照合行と原文要求行がそろっている")
    return 0


if __name__ == "__main__":
    sys.exit(main())
