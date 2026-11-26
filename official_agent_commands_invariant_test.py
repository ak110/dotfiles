import pathlib
import re
import shlex

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent
_POLICY = (
    "公式の claude・codex に独自処理を付けず、補助入口に置く。"
    " docs/development/concepts-environment.md「公式の Claude Code・Codex 起動コマンド」を参照。"
)
_OFFICIAL = r"(?:claude|codex)"


_SHELL_SEPARATORS = "();&|\n"
_COMMAND_BOUNDARIES = {"if", "then", "elif", "else", "do", "{", "!", "time", "command", "builtin"}
_OFFICIAL_NAMES = {"claude", "codex"}


def _separator(token: str) -> bool:
    return bool(token) and all(character in _SHELL_SEPARATORS for character in token)


def _overwrites_official_command(source: str) -> bool:
    lexer = shlex.shlex(source, posix=True, punctuation_chars=_SHELL_SEPARATORS)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    tokens = list(lexer)
    at_command = True
    alias = False
    for index, token in enumerate(tokens):
        if _separator(token):
            at_command, alias = True, False
            continue
        if alias and token.partition("=")[0] in _OFFICIAL_NAMES and "=" in token:
            return True
        if not at_command or token in _COMMAND_BOUNDARIES:
            continue
        following = tokens[index + 1 : index + 3]
        if token == "function" and following and following[0] in _OFFICIAL_NAMES:
            return True
        if token in _OFFICIAL_NAMES and (following[:1] == ["()"] or following == ["(", ")"]):
            return True
        at_command, alias = False, token == "alias"
    return False


def test_shell_startup_preserves_official_commands() -> None:
    for name in ("dot_bashrc", "dot_bash_profile"):
        path = _ROOT / ".chezmoi-source" / name
        assert not _overwrites_official_command(path.read_text(encoding="utf-8")), f"{path}: {_POLICY}"


@pytest.mark.parametrize("command", ["claude", "codex"])
@pytest.mark.parametrize(
    "source",
    [
        "{command}() {{ :; }}",
        "if true; then function {command} {{ :; }}; fi",
        "if true; then alias {command}='echo wrapped'; fi",
        "alias other='echo other' {command}='echo wrapped'",
        "alias other='echo other' \\n    {command}='echo wrapped'",
    ],
)
def test_shell_guard_detects_overwrites(command: str, source: str) -> None:
    assert _overwrites_official_command(source.format(command=command))


@pytest.mark.parametrize(
    "source",
    [
        "# alias claude='echo wrapped'",
        "alias myclaude='claude' mycodex='codex'",
        "claude --version; codex --version",
        'printf "%s" "alias claude=wrapped"',
    ],
)
def test_shell_guard_allows_references(source: str) -> None:
    assert not _overwrites_official_command(source)


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
