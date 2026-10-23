"""初回導入前から使える、標準ライブラリだけのdotfilesモード設定。

保存先とキーはatk configと共通にする。CLI指定は保存値を更新し、同じ実行の
chezmoiテンプレートとpost-applyへ実効値を環境変数で渡す。
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

MODES = ("full", "minimal")
MODE_ENV = "AGENT_TOOLKIT_CONFIG_DOTFILES_MODE"


def configuration_path() -> Path:
    """platformdirs.user_config_dirと同じOS別設定領域を返す。"""
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
    elif sys.platform == "darwin":
        base = home / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return base / "agent-toolkit/config.json"


def validate(value: str) -> str:
    """モードを検証し、不正値は導入前に停止する。"""
    if value not in MODES:
        raise ValueError(f"dotfiles_modeはfullまたはminimalで指定してください: {value!r}")
    return value


def _settings() -> dict[str, object]:
    try:
        value = json.loads(configuration_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"設定JSONはobjectで保存してください: {configuration_path()}")
    return value


def resolve() -> str:
    """実行の環境変数、保存値、fullの順でモードを解決する。"""
    value = os.environ.get(MODE_ENV) or _settings().get("dotfiles_mode", "full")
    if not isinstance(value, str):
        raise ValueError("dotfiles_modeは文字列で保存してください")
    return validate(value)


def select(value: str | None = None) -> str:
    """指定モードを他の設定を保持して保存し、後続プロセスへ伝達する。"""
    if value is not None:
        validate(value)
        settings = _settings()
        settings["dotfiles_mode"] = value
        path = configuration_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(settings, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    mode = value if value is not None else resolve()
    os.environ[MODE_ENV] = mode
    return mode


def is_minimal() -> bool:
    """現在の実行がminimalモードなら真を返す。"""
    return resolve() == "minimal"


def dispatch_arguments(arguments: list[str]) -> int | None:
    """依存環境が未準備でも基盤のモードget/setを公開CLIから使えるようにする。"""
    if arguments[:3] not in (["config", "get", "dotfiles_mode"], ["config", "set", "dotfiles_mode"]):
        return None
    try:
        if arguments[1] == "get" and len(arguments) == 3:
            print(resolve())
            return 0
        if arguments[1] == "set" and len(arguments) == 4:
            override = os.environ.get(MODE_ENV)
            print(f"設定を更新した: dotfiles_mode={select(arguments[3])}")
            if override:
                print(f"環境変数{MODE_ENV}が優先されます。保存値を使う場合は解除してください。", file=sys.stderr)
            return 0
    except (OSError, ValueError) as error:
        print(
            f"モード設定に失敗しました: {error}。atk config set dotfiles_mode fullまたはminimalで再実行してください。",
            file=sys.stderr,
        )
        return 2
    return None


def main() -> int:
    """初回インストーラーから、依存導入前にモードを設定する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=MODES)
    arguments = parser.parse_args()
    try:
        print(select(arguments.mode))
    except (OSError, ValueError) as error:
        print(f"モード設定に失敗しました: {error}。設定ファイルと権限を確認して再実行してください。", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
