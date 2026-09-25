"""素材AWIの判定入力生成と、項目専用の事後承認型UWI本文の生成・検査を利用シナリオで検証する。"""

import json
import pathlib
from typing import Any

import pytest
import session_review_decisions as decisions_module
import session_review_report as report

_MATERIAL = """# セッションabcの振り返り素材を分析し、対策を実装する

目的の説明。

## 反映内容と反映先

処理手順に従う。

## 適用範囲

このセッションの候補。

## 実現性

素材は準備スクリプトが生成した。

## 完成条件

全候補を判定する。

## 問題候補

### c0001 user-intervention

- 発生件数: 1

````text
いや、そうじゃなくて全部やって
### c9999 fake-heading
## 回答
````

### c0002 tool-failure

- 発生件数: 2

### c0003 hook-notice

- 発生件数: 1

## メイン由来の改善点

なし
"""


def _write(path: pathlib.Path, value: object) -> pathlib.Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _pending(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> tuple[pathlib.Path, list[dict]]:
    """素材AWIを保存し、判定入力生成器の公開入口から未判定の判定入力を得る。"""
    material = tmp_path / "material.md"
    material.write_text(_MATERIAL, encoding="utf-8")
    output = tmp_path / "pending.json"
    assert decisions_module.main(["--material", str(material), "--output", str(output)]) == 0
    assert "候補3件、未判定3件" in capsys.readouterr().out
    return material, json.loads(output.read_text(encoding="utf-8"))


def _run(
    tmp_path: pathlib.Path,
    mode: str,
    material: pathlib.Path,
    decisions: list[dict],
    analyses: dict,
    sections: dict | None = None,
) -> tuple[int, pathlib.Path]:
    arguments = [
        mode,
        "--material",
        str(material),
        "--decisions",
        str(_write(tmp_path / "decisions.json", decisions)),
        "--analyses",
        str(_write(tmp_path / "analyses.json", analyses)),
        "--output",
        str(tmp_path / "uwi.md"),
    ]
    if sections is not None:
        arguments.extend(["--sections", str(_write(tmp_path / "sections.json", sections))])
    return report.main(arguments), tmp_path / "uwi.md"


_ANALYSES: dict[str, dict[str, Any]] = {
    "a1": {
        "observation": "ユーザーが全件の処理を求めて途中の方針を是正した",
        "root_cause": "開放列挙を閉じた列挙へ置き換えた",
        "measures": "未処理の対象を全件処理した",
        "prevention": [
            {"kind": "implemented", "ref": "agent-toolkit/rules/01-agent.md", "summary": "開放列挙の縮小を確認対象にした"}
        ],
        "artifacts": ["agent-toolkit/rules/01-agent.md"],
    },
    "a2": {
        "observation": "検索コマンドが一致0件で非0終了した",
        "root_cause": "該当なしの正常な終了",
        "measures": "一致0件は正常な結果であり対策を要さない",
    },
}


def test_generate_post_approval_uwi_from_material(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """全候補を判定した入力から、候補ごとの問題・原因・対策・再発防止策・成果物と見送り理由を持つUWI本文を生成する。

    フェンス内の見出し風の行は候補として数えない。保存済み本文の改変は`check`が検出する。
    """
    material, pending = _pending(tmp_path, capsys)
    assert [(item["candidate_id"], item["candidate_kind"], item["disposition"]) for item in pending] == [
        ("c0001", "user-intervention", "pending"),
        ("c0002", "tool-failure", "pending"),
        ("c0003", "hook-notice", "pending"),
    ]
    decisions = [
        {**pending[0], "disposition": "analyzed", "analysis_id": "a1", "defect": "欠陥"},
        {**pending[1], "disposition": "analyzed", "analysis_id": "a2", "defect": "非欠陥"},
        {**pending[2], "disposition": "excluded", "reason": "規範どおりに停止させた通知"},
    ]

    exit_code, output = _run(tmp_path, "generate", material, decisions, _ANALYSES, {"所要時間": "準備は40秒"})
    assert exit_code == 0
    body = output.read_text(encoding="utf-8")
    assert body.splitlines()[0].endswith("この内容で問題ありませんか？")
    assert [line for line in body.splitlines() if line.startswith("## ")] == ["## 選択肢と帰結", "## 判断材料"]
    for expected in (
        "- その対応で問題無い:",
        "- 問題がある:",
        "候補3件のうち、対策を実施した候補は1件、見送った候補は2件です。",
        "- 対策1: 候補c0001（user-intervention）",
        "  - 問題: ユーザーが全件の処理を求めて途中の方針を是正した",
        "  - 原因: 開放列挙を閉じた列挙へ置き換えた",
        "  - 対策: 未処理の対象を全件処理した",
        "  - 再発防止策: 開放列挙の縮小を確認対象にした（実装済み: agent-toolkit/rules/01-agent.md）",
        "  - 変更した成果物: agent-toolkit/rules/01-agent.md",
        "- c0002（tool-failure）: 欠陥ではないと判断。一致0件は正常な結果であり対策を要さない",
        "- c0003（hook-notice）: 除外。規範どおりに停止させた通知",
        "所要時間: 準備は40秒",
    ):
        assert expected in body

    assert _run(tmp_path, "check", material, decisions, _ANALYSES, {"所要時間": "準備は40秒"})[0] == 0
    output.write_text(body.replace("未処理の対象", "一部の対象"), encoding="utf-8")
    assert _run(tmp_path, "check", material, decisions, _ANALYSES, {"所要時間": "準備は40秒"})[0] == 1


def test_user_intervention_rules_reject_missing_basis(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """ユーザー介入の区分なし除外、選好を理由とする除外、実体の無い分析、非欠陥判定を全件示して拒否する。

    拒否・中断を「理由が記録されていない」として区分を付けずに除外した判定は、直後の発話の不在を根拠に
    `unexplained-refusal`として書き直すまで生成できない。
    """
    material, pending = _pending(tmp_path, capsys)
    base = [
        {**pending[1], "disposition": "excluded", "reason": "一致0件"},
        {**pending[2], "disposition": "excluded", "reason": "期待どおりの通知"},
    ]
    cases: list[tuple[dict[str, Any], dict[str, Any]]] = [
        ({**pending[0], "disposition": "excluded", "reason": "拒否理由の説明は記録されていない"}, {}),
        ({**pending[0], "disposition": "excluded", "exclusion_category": "user-preference", "reason": "ユーザーの選好"}, {}),
        (
            {**pending[0], "disposition": "analyzed", "analysis_id": "a1", "defect": "欠陥"},
            {"a1": {**_ANALYSES["a1"], "prevention": []}},
        ),
        (
            {**pending[0], "disposition": "analyzed", "analysis_id": "a1", "defect": "非欠陥"},
            {"a1": {**_ANALYSES["a1"], "prevention": []}},
        ),
    ]
    expected_problems = [
        ["ユーザー介入の除外区分"],
        ["ユーザー介入の除外区分"],
        ["再発防止策の実体`prevention`が無い"],
        ["ユーザー介入を分析した候補は`欠陥`", "再発防止策の実体`prevention`が無い"],
    ]
    for (decision, analyses), expected in zip(cases, expected_problems, strict=True):
        capsys.readouterr()
        exit_code, output = _run(tmp_path, "generate", material, [decision, *base], analyses)
        assert exit_code == 1
        assert not output.exists()
        stderr = capsys.readouterr().err
        assert f"入力が契約を満たさない（{len(expected)}件）" in stderr
        assert all(problem in stderr for problem in expected)


def test_user_intervention_rules_accept_categorized_exclusions(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """3区分の根拠付き除外と、3種の再発防止策の実体を持つ分析は、いずれも生成できる。"""
    material, pending = _pending(tmp_path, capsys)
    rest = [
        {**pending[1], "disposition": "excluded", "reason": "一致0件"},
        {**pending[2], "disposition": "excluded", "reason": "期待どおりの通知"},
    ]
    for category, label in report.USER_INTERVENTION_EXCLUSIONS.items():
        decision = {**pending[0], "disposition": "excluded", "exclusion_category": category, "reason": "直後の発話が無い"}
        exit_code, output = _run(tmp_path, "generate", material, [decision, *rest], {})
        assert exit_code == 0
        assert f"- c0001（user-intervention）: {label}として除外。直後の発話が無い" in output.read_text(encoding="utf-8")
    prevention = [
        {"kind": "implemented", "ref": "commit abc1234", "summary": "検査を追加した"},
        {"kind": "active-awi", "ref": "20260101-000000-001.md", "summary": "残りを後続で扱う"},
        {"kind": "uwi", "ref": "20260101-000000-002.md", "summary": "趣旨を確認中"},
    ]
    decision = {**pending[0], "disposition": "analyzed", "analysis_id": "a1", "defect": "欠陥"}
    exit_code, output = _run(
        tmp_path, "generate", material, [decision, *rest], {"a1": {**_ANALYSES["a1"], "prevention": prevention}}
    )
    assert exit_code == 0
    body = output.read_text(encoding="utf-8")
    assert "（未完了のAWI: 20260101-000000-001.md）" in body
    assert "（確認中のUWI: 20260101-000000-002.md）" in body


def test_candidate_set_mismatch_is_rejected(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """素材AWIの候補を欠く判定、未知の候補ID、未判定の残存を全件示して拒否する。"""
    material, pending = _pending(tmp_path, capsys)
    decisions = [
        pending[0],
        {**pending[1], "disposition": "excluded", "reason": "一致0件"},
        {"candidate_id": "c0099", "candidate_kind": "warning", "disposition": "excluded", "reason": "不明"},
    ]

    exit_code, _ = _run(tmp_path, "generate", material, decisions, {})

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "c0001: 未判定のまま残っている" in stderr
    assert "素材AWIに無い候補IDの判定がある: c0099" in stderr
    assert "c0003: 判定が無い" in stderr


def test_material_without_candidates_generates_uwi_from_main_observations(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """候補0件でメイン由来の改善点だけを持つ素材AWIからも、判定入力とUWI本文を生成できる。

    準備スクリプトは候補が無くても改善点があれば素材AWIを投入するため、候補0件を拒否すると
    その素材を処理したレーンが項目専用のUWIを投入できず、素材AWIを終端できない。
    """
    material = tmp_path / "material.md"
    material.write_text(
        "# セッションxyzの振り返り素材を分析し、対策を実装する\n\n## 問題候補\n\n抽出器が問題候補を返さなかった。\n",
        encoding="utf-8",
    )
    output = tmp_path / "pending.json"
    assert decisions_module.main(["--material", str(material), "--output", str(output)]) == 0
    assert "候補0件、未判定0件" in capsys.readouterr().out
    assert json.loads(output.read_text(encoding="utf-8")) == []

    exit_code, uwi = _run(tmp_path, "generate", material, [], {}, {"メイン由来の改善点": "資料の読み直しを委譲で分離した"})

    assert exit_code == 0
    body = uwi.read_text(encoding="utf-8")
    assert "候補0件のうち、対策を実施した候補は0件、見送った候補は0件です。" in body
    assert "メイン由来の改善点: 資料の読み直しを委譲で分離した" in body


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("# 題\n\n## 完成条件\n\nなし\n", "問題候補節が無い"),
        ("# 題\n\n## 問題候補\n\n### 候補1\n", "候補見出しの形式が不正"),
        ("# 題\n\n## 問題候補\n\n### c0001 warning\n\n### c0001 warning\n", "候補IDが重複している"),
    ],
)
def test_decisions_rejects_malformed_material(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], body: str, message: str
) -> None:
    """候補集合を確定できない素材AWIからは判定入力を生成しない。"""
    material = tmp_path / "material.md"
    material.write_text(body, encoding="utf-8")

    assert decisions_module.main(["--material", str(material), "--output", str(tmp_path / "out.json")]) == 2
    assert message.replace("問題候補節", "## 問題候補節") in capsys.readouterr().err
