import argparse
import pathlib
import sys
import typing

import pytest
import yaml

from agent_toolkit._atk import run_script
from agent_toolkit._testing import git_repository

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "skills/atk-mode/scripts"))
import check_selection  # noqa: E402  # pylint: disable=wrong-import-position,wrong-import-order,import-error

_REPO_FILES = (
    "README.md",
    "src/model.py",
    "src-old/model.py",
    "docs/development/design.md",
    "agent-toolkit/agent_toolkit/_agents_server/status_file.py",
    "rust/claude-statusline/src/agents_server.rs",
    "bin/update-dotfiles",
    "bin/update-dotfiles.cmd",
    "scripts/update_dotfiles.py",
    "pytools/post_apply.py",
)


@pytest.fixture(name="env")
def fixture_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[pathlib.Path, pathlib.Path]:
    repo = tmp_path / "repo"
    for relative in _REPO_FILES:
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text("x\n", encoding="utf-8")
    git_repository.init_repository(repo, origin="https://github.com/test/repo.git")
    notes = tmp_path / "notes"
    (notes / "processing").mkdir(parents=True)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    return repo, notes


def _awi(
    notes: pathlib.Path, name: str, reflected: str, *, state: str = "processing", dependencies: list[str] | None = None
) -> None:
    (notes / state).mkdir(exist_ok=True)
    metadata = yaml.safe_dump({"target_repo": "github.com/test/repo", "depends_on": dependencies or []})
    body = (
        f"---\ntype: awi\nsource: test\n{metadata}---\n\n# 題\n\n## 反映内容と反映先\n\n{reflected}\n\n"
        "## 完成条件\n\n- `outside.py`を変える\n"
    )
    (notes / state / name).write_text(body, encoding="utf-8")


def _write_selection(
    path: pathlib.Path, decisions: list[dict[str, typing.Any]], costs: list[dict[str, typing.Any]] | None = None
) -> pathlib.Path:
    legacy = any("awi" in decision for decision in decisions)
    for decision in decisions:
        decision.setdefault("staleness" if "awi" in decision else "鮮度", {"status": "current", "later_commit_count": 0})
        public = decision.get("公開工程の書込対象", [])
        if isinstance(public, list) and public and "公開工程の規範根拠" not in decision:
            norm = path.parent / "repo" / "TEST_RULES.md"
            norm.write_text("# 検査用規範\n\n## 公開工程\n", encoding="utf-8")
            decision["公開工程の規範根拠"] = [
                {"パス": entry, "規範ファイル": "TEST_RULES.md", "節": "公開工程"} for entry in public
            ]
    if costs is None:
        lanes = {str(decision.get("lane", decision.get("レーン"))) for decision in decisions} - {"なし"}
        costs = [{"レーン": lane} for lane in sorted(lanes)]
    for row in costs:
        row.setdefault("実装秒数", 600)
        row.setdefault("統合秒数", 60)
        row.setdefault("補正前の実装秒数", row["実装秒数"])
        row.setdefault("補正前の統合秒数", row["統合秒数"])
        if "rationale" not in row:
            row.setdefault("根拠", "検査用の根拠")
    selection: dict[str, object] = {"decisions" if legacy else "選定": decisions, "レーンの所要時間": costs}
    selection["見込みの補正"] = {"係数": 1, "参照した記録": [], "除外した記録": []}
    selection["初回配分"] = _initial_allocation(decisions)
    if any(isinstance(row.get("段階"), int) and row["段階"] >= 2 for row in costs):
        selection["単一段階案の完了見込み秒数"] = 1320
    path.write_text(yaml.safe_dump(selection, allow_unicode=True), encoding="utf-8")
    return path


def _initial_allocation(decisions: list[dict[str, typing.Any]]) -> dict[str, object]:
    assignments = {str(item.get("WI", item.get("awi"))): str(item.get("レーン", item.get("lane"))) for item in decisions}

    lanes = set(assignments.values()) - {"なし"}
    candidates = [
        *assignments,
        *(f"unselected-{index}.md" for index in range(max(0, 10 * (len(lanes) - 1) + 1 - len(assignments)))),
    ]
    return {
        "候補WI": candidates,
        "レーン割当": assignments,
        "不可分成分": [{"WI": [name], "結合条件": []} for name, lane in assignments.items() if lane != "なし"],
    }


def _run(tmp_path: pathlib.Path, repo: pathlib.Path, decisions: list[dict[str, typing.Any]]) -> int:
    selection = _write_selection(tmp_path / "selection.yaml", decisions)
    return check_selection.main(["--work-dir", str(repo), str(selection)])


def _dispatch(*args: str) -> int:
    return run_script.dispatch(argparse.Namespace(script_name="pick-wi-check", script_args=["--", *args]))


def _derived(path: str, scope: str = "src/") -> dict[str, str]:
    return {
        "パス": path,
        "反映範囲": scope,
        "要求": "結果を保存する入口を新設する",
        "配置根拠": "既存model.pyの保存処理と同じ配置にする",
    }


def _overlap_record(path: str, judgment: str = "交わらない") -> dict[str, typing.Any]:
    return {
        "レーン1": "lane-01",
        "レーン2": "lane-02",
        "共通パス": path,
        "レーン1の定義": ["入力の検査"],
        "レーン2の定義": ["結果の表示"],
        "判定": judgment,
    }


def _track(repo: pathlib.Path, *relatives: str) -> None:
    for relative in relatives:
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text("x\n", encoding="utf-8")
    git_repository.init_repository(repo)
    git_repository.run_git(repo, "add", "-A")


def _write_files(repo: pathlib.Path, *relatives: str) -> None:
    for relative in relatives:
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text("x\n", encoding="utf-8")


# 本リポジトリの`pyproject.toml`。設定の削除や条件の縮小で検出が消える退行を、実物の設定で検出する。
_DOTFILES_PYPROJECT = pathlib.Path(__file__).resolve().parents[3] / "pyproject.toml"


_READ_REQUEST = (
    "docs/development/concepts.mdの全文と、変更対象の名前でgit log --grepを検索したcommit本文を計画の採否確定前に読む"
)


def _norm_spec_selection(
    tmp_path: pathlib.Path, decisions: list[dict[str, typing.Any]], specs: dict[str, str | None]
) -> pathlib.Path:
    for decision in decisions:
        spec = specs.get(decision["WI"])
        if spec is not None:
            decision["プロジェクト規範の指定"] = spec
    path = _write_selection(tmp_path / "selection.yaml", decisions)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    wis = [item["WI"] for item in decisions if item["レーン"] != "なし"]
    nodes = []
    previous: list[str] = []
    for phase in ("選定", "実装", "レビュー", "検証", "統合", "公開", "後始末", "報告"):
        nodes.append(
            {
                "ID": phase,
                "工程": phase,
                "WI": wis if phase in {"実装", "レビュー", "検証", "統合"} else [],
                "先行工程": previous,
                "資源": {"実装": "モデル", "レビュー": "モデル", "検証": "検証"}.get(phase, "直列"),
                "秒数": 1,
                "算定": "残量/速度",
                "残量": 1,
                "速度": 1,
                "根拠": "規範指定の試験用仕事量",
                "未確認前提": [],
            }
        )
        previous = [phase]
    document["全工程の見込み"] = {
        "並列数": {
            "モデル同時数": 1,
            "検証同時数": 1,
            "資源上限": 1,
            "サービス上限": 1,
            "根拠": {"資源": "試験用", "サービス": "試験用", "費用": "試験用", "検証": "試験用"},
        },
        "工程": nodes,
    }
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return path


def additional_active_items(repo: pathlib.Path, notes: pathlib.Path, lane: str, count: int) -> list[dict[str, typing.Any]]:
    """上限の算定集合に含める、互いに独立した処理対象WIを作る。  pyfltr: allow-comment"""
    items = []
    for index in range(count):
        stem = f"active-{lane}-{index}"
        target = stem + ".py"
        (repo / target).write_text("x\n", encoding="utf-8")
        _awi(notes, stem + ".md", f"`{target}`を変更する。")
        items.append(
            {
                "WI": stem + ".md",
                "レーン": lane,
                "鮮度": {"status": "current", "later_commit_count": 0},
                "書込対象": [target],
            }
        )
    return items
