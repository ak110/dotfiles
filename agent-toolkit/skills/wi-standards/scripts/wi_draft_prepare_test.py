"""公開の原稿群準備から、引用保存・構造検証・レビューへの機械的な受渡しを確かめる。"""

import argparse
import json
import pathlib

import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._testing import git_repository


def _inputs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[pathlib.Path, pathlib.Path, list[pathlib.Path]]:
    """引用付き原稿2件と共通出所・根拠、対象Gitを用意する。"""
    repo = tmp_path / "repository"
    git_repository.init_repository(repo, origin="https://github.com/example/prepare.git")
    (repo / "README.md").write_text("初期状態\n", encoding="utf-8")
    git_repository.commit_all(repo, "初期状態")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "notes"))
    (tmp_path / "notes").mkdir()
    source = tmp_path / "user-events.jsonl"
    source.write_text(
        "".join(
            json.dumps({"kind": "user", "record": "claude:source", "line": index, "text": text}, ensure_ascii=False) + "\n"
            for index, text in enumerate(("検索画面の表示を直して。", "一覧の並び順を直して。"), 1)
        ),
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence.md"
    evidence.write_text("# 既存の観測\n\n公開操作で事象を確認した。\n", encoding="utf-8")
    drafts = []
    for index in range(1, 3):
        draft = tmp_path / f"draft-{index}.md"
        draft.write_text(
            f"# 改善{index}\n\n利用する画面を改善する。\n\n"
            "## 反映内容と反映先\n\n対象リポジトリの画面表示を変更する。\n\n"
            "## 適用範囲\n\n検索と一覧の画面を対象とする。\n\n"
            "## 実現性\n\n既存の画面描画を使って実装できる。\n\n"
            "## 完成条件\n\n操作できる。\n\n## ユーザー指摘の逐語引用\n\n",
            encoding="utf-8",
        )
        drafts.append(draft)
    inputs = tmp_path / "inputs.json"
    inputs.write_text(
        json.dumps(
            {
                "source": str(source),
                "要求と出所": str(source),
                "成立性の証拠": str(evidence),
                "原稿群": [
                    {"要求識別子": f"r-{index}", "原稿": str(draft), "引用選択": [{"record": "claude:source", "line": index}]}
                    for index, draft in enumerate(drafts, 1)
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return inputs, repo, drafts


def _run(inputs: pathlib.Path, repo: pathlib.Path, output: pathlib.Path) -> int:
    """公開登録名でスクリプトを起動して生成物を得る。"""
    return run_script.dispatch(
        argparse.Namespace(
            script_name="wi-draft-prepare",
            script_args=[
                "--",
                str(inputs),
                "--target-repo",
                str(repo),
                "--output-dir",
                str(output),
            ],
        )
    )


def test_public_batch_prepares_quotes_checks_openings_and_review_inputs(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """1つの入力から2件の準備を終え、原文・根拠・読者確認の対応を生成結果から直接使える。"""
    inputs, repo, drafts = _inputs(tmp_path, monkeypatch)
    output = tmp_path / "prepared"
    status = _run(inputs, repo, output)
    assert status == 0, (output / "checks.json").read_text(encoding="utf-8")
    result = json.loads(capsys.readouterr().out)
    assert result["count"] == 2
    rows = json.loads(pathlib.Path(result["原稿"]).read_text(encoding="utf-8"))
    assert [row["要求識別子"] for row in rows] == ["r-1", "r-2"]
    assert len({row["成立性の証拠"] for row in rows}) == 1
    checks = json.loads((output / "checks.json").read_text(encoding="utf-8"))
    assert all(row["exit_code"] == 0 for row in checks)
    assert all("--dry-run" in row["argv"] for row in checks)
    for index, (draft, row) in enumerate(zip(drafts, rows, strict=True), 1):
        body = draft.read_text(encoding="utf-8")
        assert f"record=claude:source line={index}" in body
        assert pathlib.Path(row["ユーザー向け部分"]).read_text(encoding="utf-8") == body[: body.index("## 反映内容と反映先")]
        assert row["比較資料"]["現行本文"] == row["ユーザー向け部分"]
        assert "原稿は変更していない" not in json.dumps(checks, ensure_ascii=False)
    assert not list((tmp_path / "notes").iterdir())
    assert not list(output.glob("*-draft.md"))


@pytest.mark.parametrize("invalid", ["missing-event", "duplicate-body", "reserved-frontmatter"])
def test_batch_rejects_invalid_input_without_modifying_original_drafts(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], invalid: str
) -> None:
    """選択不成立・原稿重複・公開dry-run失敗では、入力原稿群を保持して診断する。"""
    inputs, repo, drafts = _inputs(tmp_path, monkeypatch)
    payload = json.loads(inputs.read_text(encoding="utf-8"))
    if invalid == "missing-event":
        payload["原稿群"][1]["引用選択"][0]["line"] = 99
    elif invalid == "duplicate-body":
        payload["原稿群"][1]["原稿"] = str(drafts[0])
    else:
        drafts[1].write_text("---\nstate: inbox\n---\n" + drafts[1].read_text(encoding="utf-8"), encoding="utf-8")
    inputs.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = [draft.read_bytes() for draft in drafts]
    assert _run(inputs, repo, tmp_path / "prepared") == 2
    assert "次の操作:" in capsys.readouterr().err
    assert [draft.read_bytes() for draft in drafts] == before
    assert not list((tmp_path / "notes").iterdir())
