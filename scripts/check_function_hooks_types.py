"""Function hooks全体をClaude Code生成型とTypeScript 5.9.3で検査する。"""

from __future__ import annotations

import argparse
import io
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile

from pytools._internal.common import run_subprocess


def _bundled_types() -> tuple[list[pathlib.Path], str]:
    """PATH上の検証対象と生成元が一致する同梱型を一度だけ選ぶ。"""
    claude = shutil.which("claude")
    result = run_subprocess([claude, "--version"], timeout=15) if claude else None
    match = re.match(r"(\d+\.\d+\.\d+)\b", result.stdout.strip()) if result and result.returncode == 0 else None
    if match is None:
        return [], "取得不能（PATH上のclaude --versionを確認）"
    version = match[1]
    temporary = pathlib.Path(tempfile.gettempdir())
    # UnixではClaude専用のユーザー別ディレクトリを一時領域の下に置く。
    bases = [temporary, *sorted(temporary.glob("claude-*"))]
    for base in bases:
        bundled = base / "bundled-skills" / version
        for path in sorted(bundled.glob("*/plugin-authoring/types/claude-code.d.ts")):
            with path.open(encoding="utf-8") as stream:
                header = stream.readline().strip()
            if header == f"// Written by Claude Code {version}.":
                return [path.resolve()], version
    return [], version


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
    version = "明示指定またはworktree内生成型"
    if args.types is not None:
        definitions = [args.types.resolve()]
    else:
        definitions = sorted((plugin / ".claude-plugin/types").rglob("*.d.ts"))
        definitions += sorted((root / ".claude-plugin/types").rglob("*.d.ts"))
        definitions += [path for path in (plugin / "types/claude-code.d.ts", root / "types/claude-code.d.ts") if path.is_file()]
        if not definitions:
            try:
                definitions, version = _bundled_types()
            except (OSError, UnicodeError) as error:
                print(
                    f"同梱型の読取に失敗した: {error}。ファイルの権限とUTF-8形式を確認し、"
                    "再実行するか、--typesまたはFUNCTION_HOOKS_TYPESで読み取れる型を指定する。",
                    file=sys.stderr,
                )
                return 2
    definitions = list(dict.fromkeys(path.resolve() for path in definitions))
    if not definitions or any(not path.is_file() for path in definitions):
        print(
            f"Claude Code生成型が見つからない（対象版: {version}）。plugin-authoringで生成し、"
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
