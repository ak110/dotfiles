"""npmがWindowsで生成する起動スクリプトの既知の形を、cmd-shimの各公開版に生成させてデータへ書き出す。

npmはWindowsのグローバル導入で、binごとに拡張子なし（sh）・`.cmd`・`.ps1`の起動スクリプトを同梱のcmd-shimで生成する。
`pytools/_internal/setup_cli_common.py`は、旧npm版の移行で削除する起動スクリプトがユーザーの編集を含まないことを、
ファイル全体がこのデータの形のどれかと一致することで確かめる。

対象はnpm 6.0.0が同梱した2.0.2以降の、npmレジストリに公開された全ての版である。
各版の配布物をnpmで隔離した場所へ導入し、binの実体の先頭行（`#!/usr/bin/env node`と、shebangなし）ごとに
起動スクリプトを生成させる。
生成物を実体の相対パスで分割し、パスを差し込む位置と区切り文字を記録する。
別の深さの相対パスでも同じ分割から生成物を再現できることを確かめてから書き出す。

使い方（npmレジストリへの接続と、nodeとnpmが要る）:
    uv run --frozen python scripts/gen_npm_cmd_shim_forms.py          # 生成または更新する
    uv run --frozen python scripts/gen_npm_cmd_shim_forms.py --check  # 再生成した結果がデータと一致するかを確かめる
"""

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from agent_toolkit._common import process_tree

_OUTPUT = Path(__file__).resolve().parent.parent / "pytools" / "_internal" / "npm_cmd_shim_forms.json"
_FIRST_VERSION = (2, 0, 2)
_PROGRAMS = {"node": ("#!/usr/bin/env node", "#!/usr/bin/env node\n"), "direct": ("shebangなし", "placeholder\n")}
"""binの実体の先頭行の種類ごとの、説明と実体の本文。cmd-shimはshebangから起動プログラムを選び、無ければ実体を直接起動する。"""
_TARGETS = ("node_modules/@cmd-shim-forms/package/bin/cli.js", "node_modules/other/lib/nested/entry.js")
"""prefixから見たbinの実体の相対パス。先頭で分割し、2つ目で分割の正しさを確かめる。"""
_SUFFIXES = ("", ".cmd", ".ps1")
_RUNNER = """\
const [moduleDir, from, to] = process.argv.slice(2)
const cmdShim = require(moduleDir)
const done = error => { if (error) { console.error(error); process.exit(1) } }
if (cmdShim.length >= 3) cmdShim(from, to, done)
else cmdShim(from, to).then(() => done(), done)
"""
"""cmd-shimを1回呼ぶnodeのスクリプト。3.x以前はcallback、4.0.0以降はPromiseを返す。"""


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(number) for number in version.split("."))


def _command(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"{name}が見つからない")
    return path


def _published_versions(npm: str, cache: Path) -> list[str]:
    result = process_tree.run_with_timeout(
        [npm, "view", "cmd-shim", "versions", "--json", "--cache", str(cache)],
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=120,
    )
    # 正式版だけを対象とする。npmは正式版のcmd-shimを同梱する。
    released = [version for version in json.loads(result.stdout) if re.fullmatch(r"\d+\.\d+\.\d+", version)]
    return sorted((version for version in released if _version_key(version) >= _FIRST_VERSION), key=_version_key)


def _install(npm: str, prefix: Path, cache: Path, versions: list[str]) -> None:
    """各版を`cmd-shim-<版>`の別名で導入する。npmはレジストリの完全性の値で配布物を検証する。"""
    process_tree.run_with_timeout(
        [
            npm,
            "install",
            "--prefix",
            str(prefix),
            "--cache",
            str(cache),
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "--no-package-lock",
            *(f"cmd-shim-{version}@npm:cmd-shim@{version}" for version in versions),
        ],
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=600,
    )


def _generate(node: str, runner: Path, module: Path, work: Path, program: str, target: str) -> dict[str, str]:
    """`work`をprefixとして、`target`を実体とするbin`cli`の起動スクリプトを生成し、拡張子ごとの本文を返す。"""
    entry = work.joinpath(*target.split("/"))
    entry.parent.mkdir(parents=True)
    entry.write_text(_PROGRAMS[program][1], encoding="utf-8")
    process_tree.run_with_timeout(
        [node, str(runner), str(module), str(entry), str(work / "cli")],
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    return {
        suffix: (work / f"cli{suffix}").read_bytes().decode("utf-8")
        for suffix in _SUFFIXES
        if (work / f"cli{suffix}").is_file()
    }


def _split(content: str, target: str) -> tuple[str, list[str]]:
    """本文を実体の相対パスの位置で分割し、区切り文字と、パスの前後の文字列を返す。"""
    found = [separator for separator in ("/", "\\") if target.replace("/", separator) in content]
    if len(found) != 1:
        raise ValueError(f"実体のパスの表記を1つに決められない: {found}")
    separator = found[0]
    parts = content.split(target.replace("/", separator))
    return separator, parts


def _forms(node: str, runner: Path, modules: Path, work: Path, versions: list[str]) -> list[dict[str, object]]:
    forms: dict[tuple[str, str, tuple[str, ...]], dict[str, list[str]]] = {}
    for version in versions:
        module = modules / f"cmd-shim-{version}"
        for program in _PROGRAMS:
            outputs = [
                _generate(node, runner, module, work / version / program / str(index), program, target)
                for index, target in enumerate(_TARGETS)
            ]
            if outputs[0].keys() != outputs[1].keys():
                raise ValueError(f"cmd-shim {version}（{program}）: 生成した拡張子が実体のパスで変わった")
            for suffix, content in outputs[0].items():
                separator, parts = _split(content, _TARGETS[0])
                if _TARGETS[1].replace("/", separator).join(parts) != outputs[1][suffix]:
                    raise ValueError(f"cmd-shim {version}（{program}）の`cli{suffix}`: 別の実体のパスで生成物を再現できない")
                members = forms.setdefault((suffix, separator, tuple(parts)), {"versions": [], "programs": []})
                members["versions"].append(version)
                if program not in members["programs"]:
                    members["programs"].append(program)
    return [
        {"suffix": suffix, "separator": separator, **members, "parts": list(parts)}
        for (suffix, separator, parts), members in sorted(
            forms.items(), key=lambda item: (item[0][0], _version_key(item[1]["versions"][0]), item[1]["programs"])
        )
    ]


def build() -> str:
    """cmd-shimの各公開版から既知の形を生成し、データのJSON本文を返す。"""
    node = _command("node")
    npm = _command("npm")
    with tempfile.TemporaryDirectory(prefix="cmd-shim-forms-") as directory:
        root = Path(directory)
        cache = root / "npm-cache"
        versions = _published_versions(npm, cache)
        _install(npm, root / "modules", cache, versions)
        runner = root / "generate.js"
        runner.write_text(_RUNNER, encoding="utf-8")
        forms = _forms(node, runner, root / "modules" / "node_modules", root / "work", versions)
    data = {
        "generated_by": "uv run --frozen python scripts/gen_npm_cmd_shim_forms.py",
        "cmd_shim_versions": versions,
        "programs": {name: description for name, (description, _) in _PROGRAMS.items()},
        "forms": forms,
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    """生成したデータを書き出すか、`--check`ではデータとの一致を確かめる。"""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="書き出さず、再生成した結果がデータと一致するかを確かめる")
    arguments = parser.parse_args()
    content = build()
    current = _OUTPUT.read_text(encoding="utf-8") if _OUTPUT.is_file() else None
    if arguments.check:
        if content != current:
            print(f"{_OUTPUT}は再生成した結果と一致しない", file=sys.stderr)
            return 1
        print(f"{_OUTPUT}は再生成した結果と一致する")
        return 0
    if content != current:
        _OUTPUT.write_text(content, encoding="utf-8", newline="\n")
        print(f"{_OUTPUT}を更新した")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
