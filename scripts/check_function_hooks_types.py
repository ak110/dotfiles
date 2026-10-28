"""Function hooks全体をClaude Code生成型とTypeScript 5.9.3で検査する。"""

from __future__ import annotations

import argparse
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile

from pytools._internal.common import run_subprocess


def main() -> int:
    """一時設定で型検査し、tscの終了状態を返す。"""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--types", type=pathlib.Path, default=os.environ.get("FUNCTION_HOOKS_TYPES"))
    args = parser.parse_args()
    root = pathlib.Path.cwd()
    plugin = root / "agent-toolkit"
    if args.types is not None:
        definitions = [args.types.resolve()]
    else:
        definitions = sorted((plugin / ".claude-plugin/types").rglob("*.d.ts"))
        definitions += sorted((root / ".claude-plugin/types").rglob("*.d.ts"))
        definitions += [path for path in (plugin / "types/claude-code.d.ts", root / "types/claude-code.d.ts") if path.is_file()]
    if not definitions or any(not path.is_file() for path in definitions):
        print(
            "Claude Code生成型が見つからない。plugin-authoringで生成し、"
            "FUNCTION_HOOKS_TYPESへclaude-code.d.tsの絶対パスを指定して再実行する。"
            "生成または指定ができない場合は、この出力を添えて開発担当へ報告する。",
            file=sys.stderr,
        )
        return 2
    sources = sorted(path.resolve() for path in (plugin / "hooks").rglob("*") if path.suffix in {".ts", ".tsx"})
    config = {
        "compilerOptions": {
            "target": "es2023",
            "lib": ["es2023"],
            "types": [],
            "module": "esnext",
            "moduleResolution": "bundler",
            "strict": True,
            "noUncheckedIndexedAccess": True,
            "noEmit": True,
            "skipLibCheck": True,
            "jsx": "react",
            "jsxFactory": "h",
            "jsxFragmentFactory": "Fragment",
            "allowImportingTsExtensions": True,
        },
        "files": [str(path) for path in [*definitions, *sources]],
    }
    with tempfile.TemporaryDirectory(prefix="function-hooks-types-") as directory:
        setting = pathlib.Path(directory) / "tsconfig.json"
        setting.write_text(json.dumps(config), encoding="utf-8")
        result = run_subprocess(
            [
                shutil.which("npx") or "npx",
                "--yes",
                "-p",
                "typescript@5.9.3",
                "tsc",
                "--project",
                str(setting),
                "--pretty",
                "false",
            ],
            timeout=120,
        )
        if result is None:
            print(
                "TypeScriptの起動に失敗した。Node.js・npmの導入と接続を確認して再実行する。"
                "解消できない場合は、この出力を添えて開発担当へ報告する。",
                file=sys.stderr,
            )
            return 2
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        return result.returncode


if __name__ == "__main__":
    sys.exit(main())
