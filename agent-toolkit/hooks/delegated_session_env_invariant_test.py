"""Function hooks moduleで委譲先の印となる環境変数を読むファイルが、判定関数を持つ1ファイルに限られることを確かめる。

hooks moduleはPythonの`is_delegated`を呼べず同じ条件を`register.ts`の`isDelegated`へ置くため、
別のファイルが環境変数を直接読むと、そのファイルだけ条件がずれて委譲先の除外が欠ける。
hooks moduleのテストはCIで実行されないため、`make test`のpytestでこの不変条件を検出する。
"""

import pathlib
import shutil

from agent_toolkit._common import delegated_session

_HOOKS_DIR = pathlib.Path(__file__).resolve().parent
_JUDGMENT_FILE = "register.ts"
_ENV_NAMES = (delegated_session.DELEGATED_SESSION_ENV, delegated_session.OWNER_SESSION_ENV)


def _env_readers(hooks_dir: pathlib.Path) -> dict[str, set[str]]:
    """テスト以外の`*.ts`・`*.tsx`のうち2つの環境変数名を含むファイルと、含む名前を返す。"""
    readers: dict[str, set[str]] = {}
    for path in sorted(hooks_dir.rglob("*")):
        if path.suffix not in (".ts", ".tsx") or path.name.endswith((".test.ts", ".test.tsx")):
            continue
        text = path.read_text(encoding="utf-8")
        names = {name for name in _ENV_NAMES if name in text}
        if names:
            readers[path.relative_to(hooks_dir).as_posix()] = names
    return readers


def test_only_judgment_file_reads_delegated_session_env() -> None:
    """2つの環境変数名を書くファイルが判定関数の1ファイルだけで、その名前がPython側の定義と一致する。"""
    assert _env_readers(_HOOKS_DIR) == {_JUDGMENT_FILE: set(_ENV_NAMES)}
    text = (_HOOKS_DIR / _JUDGMENT_FILE).read_text(encoding="utf-8")
    assert "async function isDelegated(" in text
    for name in _ENV_NAMES:
        assert f'$.env.get("{name}")' in text


def test_env_read_outside_judgment_file_is_detected(tmp_path: pathlib.Path) -> None:
    """判定関数を持たないファイルが環境変数を直接読むと検出し、テストのファイルは対象から外す。"""
    shutil.copy(_HOOKS_DIR / _JUDGMENT_FILE, tmp_path / _JUDGMENT_FILE)
    (tmp_path / "feature.tsx").write_text(
        f'export const read = ($) => $.env.get("{delegated_session.OWNER_SESSION_ENV}");\n', encoding="utf-8"
    )
    (tmp_path / "feature.test.ts").write_text(
        f'mock.env(on, {{ {delegated_session.DELEGATED_SESSION_ENV}: "1" }});\n', encoding="utf-8"
    )
    expected = {_JUDGMENT_FILE: set(_ENV_NAMES), "feature.tsx": {delegated_session.OWNER_SESSION_ENV}}
    assert _env_readers(tmp_path) == expected
