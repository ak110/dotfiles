#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""`scripts/`配下スクリプトと`[project.scripts]`が参照するモジュール・関数の解決可能性を検査する。

スクリプトを実行しない静的解析（`ast.parse`）のみで判定し、副作用を起こさない。
対象種別ごとに検査方式を分ける。

- 対象種別1: `scripts/`配下のPEP 723単独実行スクリプト（`*_test.py`を除く。shebang
  `#!/usr/bin/env -S uv run --script`の有無で判定する）。`import`文・`from ... import`文を
  `ast.parse`で抽出し、各モジュール名が標準ライブラリ（`sys.stdlib_module_names`）・
  PEP 723 headerの`dependencies`に列挙された外部パッケージ・同一ディレクトリに実在する`.py`
  ファイルのいずれかに該当するかを照合する。いずれにも該当しない場合を解決不能として報告する
- 対象種別2: `pyproject.toml`の`[project.scripts]`が参照する`module:function`形式。
  参照先モジュールファイルが実在するか、当該ファイル内に対象関数の定義（または再エクスポートによる
  束縛）が存在するかを`ast.parse`で確認する。プロジェクト依存の解決は`--no-project`環境では
  成立しないため対象外とする

スクリプトをimportまたは実行する方式は採らない。生成処理・ファイル書き込みなどの副作用を
実行し得るうえ、`--help`への対応も保証されていないため。

対象一覧は`scripts/`配下の走査と`pyproject.toml`から機械的に取得し、本スクリプト側に
重複した一覧を持たない。

注記: `agent-toolkit/hooks/hooks.json`の登録コマンドは`claude_hook.py`単一エントリポイント
経由のため、hookスクリプト個別名を登録定義から機械取得できない。「自動処理の登録定義から
一覧を機械取得する」設計は現構造では成立しないため、`scripts/`配下の走査で代替する。

error・warning区分: 本スクリプトが報告する全項目は`error`区分（exit code 1）とする。
importが解決できない状態は起動時に確実に失敗する致命的な問題であり、警告に留める余地が無いため。
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
import tomllib

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
_PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"

_SHEBANG = "#!/usr/bin/env -S uv run --script"

# PEP 723インラインメタデータブロックの抽出パターン（公式仕様のリファレンス実装に準拠）。
_PEP723_BLOCK_RE = re.compile(r"(?m)^# /// (?P<type>[A-Za-z0-9-]+)$\s(?P<content>(?:^#(?:| .*)$\s)+)^# ///$")


def _is_pep723_script(text: str) -> bool:
    """本文の先頭行がPEP 723単独実行スクリプトのshebangかを判定する。"""
    first_line = text.splitlines()[0] if text else ""
    return first_line.startswith(_SHEBANG)


def _dependency_import_name(requirement: str) -> str:
    """依存指定文字列（`requests>=2`等）からトップレベルimport名相当の名前を抽出する。"""
    name = re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].strip()
    return name.replace("-", "_")


def _read_pep723_dependencies(text: str) -> set[str]:
    """PEP 723インラインメタデータの`dependencies`が示すimport名集合を返す。"""
    for match in _PEP723_BLOCK_RE.finditer(text):
        if match.group("type") != "script":
            continue
        content = "".join(
            line[2:] if line.startswith("# ") else line[1:] for line in match.group("content").splitlines(keepends=True)
        )
        metadata = tomllib.loads(content)
        deps = metadata.get("dependencies", [])
        return {_dependency_import_name(dep) for dep in deps if isinstance(dep, str)}
    return set()


def _extract_imported_top_level_names(text: str, script_path: pathlib.Path) -> set[str]:
    """本文からimportされたトップレベルモジュール名を`ast.parse`で抽出する。"""
    tree = ast.parse(text, filename=str(script_path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names.add(node.module.split(".", 1)[0])
    return names


def _check_scripts_dir() -> list[str]:
    """`scripts/`配下のPEP 723スクリプトのimport解決可能性を検査する。"""
    problems: list[str] = []
    if not _SCRIPTS_DIR.is_dir():
        return problems
    stdlib_names = sys.stdlib_module_names
    local_modules = {p.stem for p in _SCRIPTS_DIR.glob("*.py")}
    # `sys.path.insert(0, str(_REPO_ROOT))`でリポジトリルートを`sys.path`へ追加してから
    # `pytools`等のリポジトリ直下パッケージをimportする既存パターン（`gen-completions.py`等）に対応する。
    repo_root_packages = {p.stem for p in _REPO_ROOT.glob("*.py")} | {
        p.name for p in _REPO_ROOT.iterdir() if p.is_dir() and (p / "__init__.py").is_file()
    }
    for script_path in sorted(_SCRIPTS_DIR.glob("*.py")):
        if script_path.name.endswith("_test.py"):
            continue
        text = script_path.read_text(encoding="utf-8")
        if not _is_pep723_script(text):
            continue
        dependencies = _read_pep723_dependencies(text)
        try:
            imported = _extract_imported_top_level_names(text, script_path)
        except SyntaxError as exc:
            problems.append(f"{script_path.relative_to(_REPO_ROOT)}: 構文解析に失敗: {exc}")
            continue
        for name in sorted(imported):
            if name in stdlib_names or name in dependencies or name in local_modules or name in repo_root_packages:
                continue
            problems.append(f"{script_path.relative_to(_REPO_ROOT)}: 解決不能なimport `{name}`")
    return problems


def _resolve_module_file(module: str) -> pathlib.Path | None:
    """`pytools.foo`形式のモジュール名から実体ファイルを返す（単一ファイル・サブパッケージ双方に対応）。"""
    base = _REPO_ROOT / pathlib.Path(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _top_level_bound_names(tree: ast.Module) -> set[str]:
    """モジュールのトップレベルで束縛される名前一覧を返す（定義・代入・importの再エクスポートを含む）。"""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _check_project_scripts() -> list[str]:
    """`pyproject.toml`の`[project.scripts]`が参照するモジュール・関数の実在を検査する。"""
    problems: list[str] = []
    if not _PYPROJECT_PATH.is_file():
        return problems
    data = tomllib.loads(_PYPROJECT_PATH.read_text(encoding="utf-8"))
    scripts = data.get("project", {}).get("scripts", {})
    for name, target in sorted(scripts.items()):
        if not isinstance(target, str) or ":" not in target:
            problems.append(f"[project.scripts] {name}: `module:function`形式でない: {target}")
            continue
        module, _sep, function = target.partition(":")
        module_file = _resolve_module_file(module)
        if module_file is None:
            problems.append(f"[project.scripts] {name}: モジュールファイルが実在しない: {module}")
            continue
        try:
            tree = ast.parse(module_file.read_text(encoding="utf-8"), filename=str(module_file))
        except SyntaxError as exc:
            problems.append(f"[project.scripts] {name}: {module_file}の構文解析に失敗: {exc}")
            continue
        if function not in _top_level_bound_names(tree):
            problems.append(f"[project.scripts] {name}: {module_file}に`{function}`の定義が見当たらない")
    return problems


def main() -> int:
    """`scripts/`配下と`[project.scripts]`のimport解決可能性を検査し、解決不能な箇所をstderrへ出力する。"""
    problems = _check_scripts_dir() + _check_project_scripts()
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
