"""LinuxとWindowsのラッパーの生成・再実行と引数の受け渡し。"""

import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys


def test_generated_pair_passes_arguments_and_preserves_existing_files(tmp_path: pathlib.Path) -> None:
    """再生成せず、両形式の行末と実行対象OSでの子の結果を保つ。"""
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    generator = script_dir / "new_bin_cmd.py"
    shutil.copy2(pathlib.Path(__file__).with_name(generator.name), generator)
    child = tmp_path / "child with space.py"
    child.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\nraise SystemExit(23)\n", encoding="utf-8")

    argv = [sys.executable, str(generator), "wrapper", "--", f'"{sys.executable}"', f'"{child}"']
    subprocess.run(argv, check=True, capture_output=True)
    bash, cmd = bin_dir / "wrapper", bin_dir / "wrapper.cmd"
    bash_text, cmd_text = bash.read_bytes(), cmd.read_bytes()
    assert b"\r" not in bash_text and bash_text.decode("utf-8").splitlines()[0] == "#!/usr/bin/env bash"
    assert cmd_text.count(b"\r\n") == cmd_text.count(b"\n")
    assert cmd_text.decode("cp932").splitlines()[0] == "@echo off"
    if os.name != "nt":
        assert bash.stat().st_mode & stat.S_IXUSR

    subprocess.run([sys.executable, str(generator), "wrapper", "different"], check=True, capture_output=True)
    assert (bash.read_bytes(), cmd.read_bytes()) == (bash_text, cmd_text)

    payload = ["-f", "two words", "next"]
    command = (
        f'cmd.exe /d /s /c "{subprocess.list2cmdline([str(cmd), *payload])}"' if os.name == "nt" else [str(bash), *payload]
    )
    result = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 23, result.stderr
    assert json.loads(result.stdout) == payload
