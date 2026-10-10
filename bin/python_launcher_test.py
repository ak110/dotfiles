"""`bin/python`がPATH末尾から`python3`を起動し、前方の`python`を隠さない契約。

Debianの`/etc/profile`はログインシェルでPATHを置き換えるため、隔離したPATHを保つよう`bash -c`で起動する。
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bin/pythonはLinux用")

_BIN = pathlib.Path(__file__).resolve().parent


def _bash(command: str, path_entries: list[pathlib.Path]) -> subprocess.CompletedProcess[str]:
    """`.bashrc`と同じく作業ツリーの`bin`をPATHの末尾に置き、指定したディレクトリだけを前方へ並べて実行する。"""
    path = os.pathsep.join(str(entry) for entry in [*path_entries, _BIN])
    return subprocess.run(
        ["/bin/bash", "-c", command],
        env=os.environ | {"PATH": path},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def _python3_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """テストを実行中のPython 3だけを`python3`として置いたディレクトリを返す。"""
    directory = tmp_path / "python3-only"
    directory.mkdir()
    (directory / "python3").symlink_to(sys.executable)
    return directory


def test_runs_python3_from_path_end(tmp_path: pathlib.Path) -> None:
    """PATHの他の位置に`python`が無ければ、末尾の`bin/python`が`python3`を起動する。"""
    result = _bash("python -c 'import sys; print(sys.version_info[0])'", [_python3_dir(tmp_path)])

    assert result.returncode == 0, result.stderr
    assert result.stdout == "3\n"


def test_passes_arguments_and_exit_code(tmp_path: pathlib.Path) -> None:
    """スクリプトの引数をそのまま渡し、スクリプトの終了コードを返す。"""
    script = tmp_path / "script.py"
    script.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\nraise SystemExit(7)\n", encoding="utf-8")

    result = _bash(f"python '{script}' 'a b' --flag ''", [_python3_dir(tmp_path)])

    assert result.returncode == 7, result.stderr
    assert json.loads(result.stdout) == ["a b", "--flag", ""]


def test_prefers_python_earlier_in_path(tmp_path: pathlib.Path) -> None:
    """PATHの前方に別の`python`があれば、そちらが実行される。"""
    front = tmp_path / "front"
    front.mkdir()
    (front / "python").write_text("#!/bin/bash\necho front-python\n", encoding="utf-8")
    (front / "python").chmod(0o755)

    result = _bash("python -c 'print(1)'", [front, _python3_dir(tmp_path)])

    assert result.returncode == 0, result.stderr
    assert result.stdout == "front-python\n"


def test_exits_127_without_python3(tmp_path: pathlib.Path) -> None:
    """PATHに`python3`が無ければ、自身を再帰起動せず終了コード127と`python3`が無い旨の標準エラーで終わる。"""
    empty = tmp_path / "empty"
    empty.mkdir()

    result = _bash("python -c 'print(1)'", [empty])

    assert result.returncode == 127
    assert result.stdout == ""
    assert "python3" in result.stderr
