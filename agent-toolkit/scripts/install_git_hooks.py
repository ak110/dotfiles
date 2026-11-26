"""公式Codexの実行環境へ固定パスのcommit検査を導入する。pyfltr: allow-comment"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_toolkit._common import commit_hook_environment, host_homes  # noqa: E402  # pylint: disable=wrong-import-position


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true", help="検査用の設定を消し、ユーザーの設定を保持する")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    directory = None if args.remove else host_homes.commit_hook_directory(Path.home(), "codex")
    if directory is not None:
        commit_hook_environment.install(directory, root)
    if commit_hook_environment.merge_codex_config(codex_home / "config.toml", directory):
        print("changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
