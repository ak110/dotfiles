import os
import pathlib
import re
import shutil
import subprocess
from collections.abc import Callable

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent
_POLICY = (
    "公式の claude・codex に独自処理を付けず、補助入口に置く。"
    " docs/development/concepts-environment.md「公式の Claude Code・Codex 起動コマンド」を参照。"
)
_OFFICIAL = r"(?:claude|codex)"


@pytest.fixture
def bash(host_environ: Callable[[], dict[str, str]]) -> tuple[str, dict[str, str]]:
    host = host_environ()
    if os.name == "nt":
        # pyfltr: allow-comment -- System32のbash.exeはWSL起動用であり、Windows CIではGit Bashを使う。
        paths = [
            pathlib.Path(host[key]) / "Git" / "bin" / "bash.exe" for key in ("ProgramFiles", "ProgramW6432") if host.get(key)
        ]
        if git := shutil.which("git", path=host.get("PATH", "")):
            paths.insert(0, pathlib.Path(git).parent.parent / "bin" / "bash.exe")
        executable = next((str(path) for path in paths if path.is_file()), None)
    else:
        executable = shutil.which("bash", path=host.get("PATH", ""))
    if executable is None:
        if host.get("CI") == "true":
            pytest.fail("CIではBash（WindowsではGit Bash）が必要。")
        pytest.skip("公式名の起動設定を評価するBashが見つからない。")
    env = {key: host[key] for key in ("SystemRoot", "WINDIR") if key in host}
    return executable, env


def _shell_overrides(bash: tuple[str, dict[str, str]], home: pathlib.Path, startup: str) -> list[str]:
    executable, platform_env = bash
    result = subprocess.run(
        [
            executable,
            "--noprofile",
            "--norc",
            "-ic",
            r"""
source "./$1" || exit
for name in claude codex; do
    case "$(builtin type -t "$name")" in
        alias|function) printf 'override:%s\n' "$name" ;;
    esac
done
printf 'startup-checked\n'
""",
            "bash",
            startup,
        ],
        cwd=home,
        env={**platform_env, "HOME": home.as_posix(), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TERM": "dumb"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=True,
    )
    lines = result.stdout.splitlines()
    assert lines and lines[-1] == "startup-checked", f"Bashの検査が完了しなかった: {result.stderr}"
    return [line.removeprefix("override:") for line in lines if line.startswith("override:")]


@pytest.mark.windows
@pytest.mark.parametrize("startup", [".bashrc", ".bash_profile"])
def test_shell_startup_preserves_official_commands(
    bash: tuple[str, dict[str, str]],
    tmp_path: pathlib.Path,
    startup: str,
) -> None:
    for name in (".bashrc", ".bash_profile"):
        shutil.copyfile(_ROOT / ".chezmoi-source" / f"dot_{name[1:]}", tmp_path / name)
    assert not _shell_overrides(bash, tmp_path, startup), _POLICY


@pytest.mark.windows
@pytest.mark.parametrize("command", ["claude", "codex"])
@pytest.mark.parametrize(
    "source",
    [
        "{command}() {{ :; }}",
        "{command}()\n{{\n  :\n}}",
        "if true; then function {command} {{ :; }}; fi",
        "if true; then alias {command}='echo wrapped'; fi",
        "alias other='echo other' {command}='echo wrapped'",
        "alias other='echo other' " + chr(92) + "\n  {command}='echo wrapped'",
        'name={command}; alias "$name=echo wrapped"',
    ],
)
def test_shell_guard_detects_overwrites(
    bash: tuple[str, dict[str, str]],
    tmp_path: pathlib.Path,
    command: str,
    source: str,
) -> None:
    (tmp_path / ".bashrc").write_text(source.format(command=command) + "\n", encoding="utf-8", newline="\n")
    assert _shell_overrides(bash, tmp_path, ".bashrc") == [command]


@pytest.mark.windows
def test_shell_guard_allows_references(bash: tuple[str, dict[str, str]], tmp_path: pathlib.Path) -> None:
    (tmp_path / ".bashrc").write_text(
        "# alias claude='echo wrapped'\nalias myclaude='claude' mycodex='codex'\ntype -t claude codex || :\n",
        encoding="utf-8",
        newline="\n",
    )
    assert not _shell_overrides(bash, tmp_path, ".bashrc")


def test_bin_preserves_official_commands() -> None:
    names = {f"{command}{suffix}" for command in ("claude", "codex") for suffix in ("", ".cmd", ".ps1", ".exe", ".bat")}
    for path in (_ROOT / "bin").iterdir():
        assert path.name.lower() not in names, f"{path}: {_POLICY}"


def test_windows_setup_preserves_official_commands() -> None:
    macro = re.compile(rf"""\bdoskey(?:\.exe)?\b[^\r\n]*?['"]?\b{_OFFICIAL}\s*=""", re.IGNORECASE)
    autorun = re.compile(rf"""\bAutoRun\b[^\r\n]*?\b{_OFFICIAL}(?:\.(?:cmd|ps1|bat|exe))?\b""", re.IGNORECASE)
    for directory in (".chezmoi-source", "bin", "pytools", "scripts", "libexec"):
        for path in (_ROOT / directory).rglob("*"):
            if path.is_file() and (path.suffix.lower() in {".py", ".ps1", ".cmd", ".bat"} or path.name.endswith(".ps1.tmpl")):
                source = path.read_text(encoding="utf-8-sig", errors="surrogateescape")
                assert not (macro.search(source) or autorun.search(source)), f"{path}: {_POLICY}"
