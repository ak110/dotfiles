import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent
_POLICY = (
    "公式の claude・codex に独自処理を付けず、補助入口に置く。"
    " docs/development/concepts-environment.md「公式の Claude Code・Codex 起動コマンド」を参照。"
)
_OFFICIAL = r"(?:claude|codex)"


def test_shell_startup_preserves_official_commands() -> None:
    definition = re.compile(
        rf"""^\s*(?:function\s+{_OFFICIAL}\b|{_OFFICIAL}\s*\(\s*\)|alias\s+['"]?{_OFFICIAL}=)""",
        re.MULTILINE,
    )
    for name in ("dot_bashrc", "dot_bash_profile"):
        path = _ROOT / ".chezmoi-source" / name
        assert definition.search(path.read_text(encoding="utf-8")) is None, f"{path}: {_POLICY}"


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
