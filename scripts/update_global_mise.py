import os
import re
import shutil
import tempfile
import tomllib
from pathlib import Path

from agent_toolkit._common import process_tree


def update(root: Path) -> None:
    source = root / ".chezmoi-source/dot_config/mise"
    template = source / "config.toml.tmpl"
    environment = {**os.environ, "AGENT_TOOLKIT_CONFIG_DOTFILES_MODE": "full"}
    rendered = process_tree.run_with_timeout(
        ["chezmoi", "execute-template", "--file", str(template)],
        env=environment,
        check=True,
        capture_output=True,
        encoding="utf-8",
        timeout=60,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="dotfiles-mise-") as directory:
        temporary = Path(directory)

        configuration = temporary / "mise.toml"
        configuration.write_text(rendered, encoding="utf-8")
        shutil.copy2(source / "mise.lock", temporary / "mise.lock")
        existing_locks = source / "dot_mise/locks"
        # aubeの参照パスはlockfile相対の`.mise/locks`。chezmoiの属性を外して戻す。
        if existing_locks.is_dir():
            for entry in existing_locks.iterdir():
                if entry.is_dir():
                    shutil.copytree(entry, temporary / ".mise/locks" / entry.name.removeprefix("exact_"))

        isolated_config = temporary / "user-config"
        isolated_config.mkdir()
        environment["MISE_CONFIG_DIR"] = str(isolated_config)
        process_tree.run_with_timeout(["mise", "trust", str(configuration)], env=environment, check=True, timeout=60)
        for arguments in (
            ["upgrade", "--bump", "dotnet"],
            ["lock", "--bump", "--platform", "linux-x64,windows-x64"],
        ):
            process_tree.run_with_timeout(["mise", *arguments], cwd=temporary, env=environment, check=True, timeout=1200)
        version = tomllib.loads(configuration.read_text(encoding="utf-8"))["tools"]["dotnet"]
        original = template.read_text(encoding="utf-8")
        replacement, count = re.subn(r'^dotnet = "[^"]+"$', f'dotnet = "{version}"', original, flags=re.MULTILINE)
        if count != 1:
            raise ValueError("配布テンプレート内のdotnet指定を一意に更新できません")

        staged_locks = temporary / ".mise/locks"
        if staged_locks.is_dir():
            if existing_locks.exists():
                shutil.rmtree(existing_locks)
            for entry in staged_locks.iterdir():
                if entry.is_dir():
                    shutil.copytree(entry, existing_locks / ("exact_" + entry.name))
        shutil.copy2(temporary / "mise.lock", source / "mise.lock")
        template.write_text(replacement, encoding="utf-8")


def main() -> int:
    update(Path(__file__).resolve().parent.parent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
