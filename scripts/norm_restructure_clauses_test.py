"""`norm_restructure_clauses.py`の条文の切り出し、台帳の分割出力および由来の判定を検証する。

台帳の行数は、基準commitへ同じ切り出し規則を再実行した件数と一致することを完成条件とする。
切り出し規則が変わると台帳の行と条文の対応が崩れるため、規則そのものを公開CLI経由で固定する。
"""

from __future__ import annotations

import pathlib
import subprocess

import norm_restructure_clauses
import pytest

_DOC = """---
name: sample
description: 説明
---

# 題

冒頭の段落の1行目。
冒頭の段落の2行目。

## 節A

- 箇条1
  - 子の箇条
  続きの行
- 箇条2

  空行の後の字下げした続き

1. 手順1
2. 手順2

| 列1 | 列2 |
| --- | --- |
| 行1 | 値 |
| 行2 | 値 |

```text
# フェンス内の井桁で始まる行

フェンス内の空行の後
```

### 節B

<例>
例1
</例>
"""


def _git(repo: pathlib.Path, *args: str, message: str | None = None) -> None:
    command = ["git", "-C", str(repo), *args]
    if message is not None:
        command.extend(["-m", message])
    subprocess.run(command, check=True, capture_output=True)


@pytest.fixture(name="repo")
def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """対象集合のファイルと対象外のファイルを持つGitリポジトリを作成する。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    skill = repo / "agent-toolkit" / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(_DOC, encoding="utf-8")
    (repo / "agent-toolkit" / "rules").mkdir(parents=True)
    (repo / "agent-toolkit" / "rules" / "01-agent.md").write_text("# 規範\n\n人間が書いた条文である。\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "outside.md").write_text("対象外の文書である。\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", message="初版")
    rules = repo / "agent-toolkit" / "rules" / "01-agent.md"
    rules.write_text(rules.read_text(encoding="utf-8") + "\nエージェントが追記した条文である。\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", message="追記\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    return repo


def _run(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *args: str) -> str:
    monkeypatch.chdir(repo)
    assert norm_restructure_clauses.main(["HEAD", *args]) == 0
    return capsys.readouterr().out


def test_clause_units_and_keys(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """見出しを除くブロック単位で切り出し、対象集合の外のファイルを含めない。"""
    out = _run(repo, monkeypatch, capsys)
    rows = [line.split("\t") for line in out.splitlines()]
    assert rows[0] == list(norm_restructure_clauses.LEDGER_COLUMNS)
    keys = [row[0] for row in rows[1:]]
    excerpts = {row[0]: row[1] for row in rows[1:]}
    skill = "agent-toolkit/skills/demo/SKILL.md"
    assert keys == [
        "agent-toolkit/rules/01-agent.md#規範#1",
        "agent-toolkit/rules/01-agent.md#規範#2",
        f"{skill}##1",
        f"{skill}#題#1",
        f"{skill}#題 > 節A#1",
        f"{skill}#題 > 節A#2",
        f"{skill}#題 > 節A#3",
        f"{skill}#題 > 節A#4",
        f"{skill}#題 > 節A#5",
        f"{skill}#題 > 節A#6",
        f"{skill}#題 > 節A#7",
        f"{skill}#題 > 節A > 節B#1",
    ]
    # 字下げした子の箇条と続きの行、空行の後の字下げした続きは同じ箇条に含まれる
    assert excerpts[f"{skill}#題 > 節A#1"].startswith("- 箇条1 - 子の箇条 続きの行")
    assert "空行の後の字下げした続き" in excerpts[f"{skill}#題 > 節A#2"]
    # 段落は複数行を1件とし、フェンス内の`#`で始まる行と空行はフェンスのブロックに含まれる
    assert excerpts[f"{skill}#題#1"] == "冒頭の段落の1行目。 冒頭の段落の2行目。"
    assert excerpts[f"{skill}#題 > 節A#7"].startswith("```text # フェンス内の井桁で始まる行")
    # 表の見出し行と区切り行は数えず、本体の行を1件ずつ数える
    assert excerpts[f"{skill}#題 > 節A#5"].startswith("| 行1 |")
    assert all(len(excerpt) <= 40 for excerpt in excerpts.values())


def test_count_matches_rows(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """`--count`の件数は台帳の行数（見出し行を除く）と一致する。"""
    rows = _run(repo, monkeypatch, capsys).splitlines()
    assert _run(repo, monkeypatch, capsys, "--count").strip() == str(len(rows) - 1)


def test_split_dir_and_origin(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """条文を持つファイルごとに台帳を分け、trailerの有無で由来列の初期値を分ける。"""
    out_dir = repo.parent / "ledger"
    _run(repo, monkeypatch, capsys, "--origin", "--jobs", "2", "--split-dir", str(out_dir))
    assert sorted(path.name for path in out_dir.iterdir()) == [
        "agent-toolkit__rules__01-agent.md.tsv",
        "agent-toolkit__skills__demo__SKILL.md.tsv",
    ]
    rows = [
        line.split("\t")
        for line in (out_dir / "agent-toolkit__rules__01-agent.md.tsv").read_text(encoding="utf-8").splitlines()[1:]
    ]
    origin_column = norm_restructure_clauses.LEDGER_COLUMNS.index("由来")
    origins = {row[1]: row[origin_column] for row in rows}
    assert origins["人間が書いた条文である。"].startswith("未確定: 導入commit")
    assert origins["エージェントが追記した条文である。"].startswith("エージェント由来: 導入commit")
