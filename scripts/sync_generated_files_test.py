"""sync_generated_filesのテスト。"""

from pathlib import Path

import pytest
import sync_generated_files as subject
from agent_toolkit._common import read_triggers


def test_runs_all_generators_in_order(monkeypatch) -> None:
    called: list[str] = []

    def fake_run(path: str) -> int:
        called.append(path)
        return 0

    monkeypatch.setattr(subject, "run_generator", fake_run)
    monkeypatch.setattr(subject, "sync_quality_principles", lambda: called.append("quality"))
    monkeypatch.setattr(subject, "sync_return_contracts", lambda: called.append("returns"))
    assert subject.main([]) == 0
    assert called == ["quality", "returns", *subject.GENERATORS]


def test_aggregates_failures_without_stopping(monkeypatch, capsys) -> None:
    called: list[str] = []

    def fake_run(path: str) -> int:
        called.append(path)
        return int(path in subject.GENERATORS[::2])

    monkeypatch.setattr(subject, "run_generator", fake_run)
    monkeypatch.setattr(subject, "sync_quality_principles", lambda: None)
    monkeypatch.setattr(subject, "sync_return_contracts", lambda: None)
    assert subject.main([]) == 1
    assert called == list(subject.GENERATORS)
    assert subject.GENERATORS[0] in capsys.readouterr().err


@pytest.mark.parametrize("arguments,code", [(["--help"], 0), (["--check"], 2), (["--unknown"], 2), (["extra"], 2)])
def test_input_is_handled_before_generation(monkeypatch, capsys, arguments, code) -> None:
    """公開された起動コマンドの入力検査では生成器の子プロセスを起動しない。"""
    calls = []
    monkeypatch.setattr(subject.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(subject, "sync_quality_principles", lambda: calls.append("quality"))
    monkeypatch.setattr(subject, "sync_return_contracts", lambda: calls.append("returns"))
    with pytest.raises(SystemExit) as error:
        subject.main(arguments)
    assert error.value.code == code
    assert not calls
    output = capsys.readouterr()
    assert output.out if code == 0 else output.err


def test_quality_source_updates_normal_and_lightweight_delivery(tmp_path: Path) -> None:
    """原則の正本改訂が全同梱先へ届き、品質外の認可本文と軽量配送範囲を保つ。"""
    source = tmp_path / subject.QUALITY_SOURCE
    source.parent.mkdir(parents=True)
    source.write_text(
        "# 品質\n\n## 役割分担\n\n役割本文。\n\n## QCDと3段判定\n\n品質本文。\n\n"
        "### 設計の判定順\n\n順序本文。\n\n## 規定の区分と標示\n\n制約本文。\n\n"
        "## 事実と推論\n\n帰属本文。\n\n### 結果の返却\n\n返却の条件。\n\n"
        "## 要件と認可\n\n配送対象外。\n",
        encoding="utf-8",
    )
    normal = tmp_path / "agent-toolkit/rules/01-agent.md"
    lightweight = tmp_path / "agent-toolkit/share/agents-server-base.md"
    for path in (normal, lightweight):
        path.parent.mkdir(parents=True, exist_ok=True)
        keys = [key for relative, key, _, _ in subject.QUALITY_TARGETS if tmp_path / relative == path]
        blocks = "\n".join(
            f"<!-- quality-principles:{key}:start -->\n旧本文。\n<!-- quality-principles:{key}:end -->" for key in keys
        )
        path.write_text(f"# 固有規範\n\n認可本文。\n{blocks}\n末尾の規約。\n", encoding="utf-8")
    subject.sync_quality_principles(tmp_path)
    normal_text = normal.read_text(encoding="utf-8")
    lightweight_text = lightweight.read_text(encoding="utf-8")
    assert "役割本文。" in normal_text
    assert "#### 設計の判定順\n\n順序本文。" in normal_text
    assert "### 結果の返却\n\n返却の条件。" in normal_text
    assert "返却の条件。" in lightweight_text
    assert "帰属本文。" not in lightweight_text
    assert "品質本文。" not in lightweight_text
    for text in (normal_text, lightweight_text):
        assert "認可本文。" in text
        assert text.endswith("末尾の規約。\n")
        assert "配送対象外。" not in text
        assert "旧本文。" not in text
    source.write_text(source.read_text(encoding="utf-8").replace("返却の条件。", "改訂した返却の条件。"), encoding="utf-8")
    subject.sync_quality_principles(tmp_path)
    for path in (normal, lightweight):
        assert "改訂した返却の条件。" in path.read_text(encoding="utf-8")


def test_quality_sync_failure_stops_downstream_generation(monkeypatch, capsys) -> None:
    """品質核を同期できないときは、旧本文の配布物を生成する前に原因を返す。"""

    def fail() -> None:
        raise ValueError("factsの生成境界が一意でない")

    calls: list[str] = []
    monkeypatch.setattr(subject, "sync_quality_principles", fail)
    monkeypatch.setattr(subject, "run_generator", calls.append)
    assert subject.main([]) == 1
    assert not calls
    assert "factsの生成境界が一意でない" in capsys.readouterr().err


def test_return_targets_cover_all_roles_and_declared_callers() -> None:
    """役割群全体と各役割が宣言する呼出側を生成対象表へ対応付ける。"""
    plugin = subject.REPO_ROOT / "agent-toolkit"
    sources = {path.name.removesuffix(".subagent.md"): path for path in (plugin / "share").glob("*.subagent.md")}
    targets = {role: relative for role, relative, _title, _level in subject.RETURN_TARGETS}
    assert len(targets) == len(subject.RETURN_TARGETS)
    assert targets.keys() == sources.keys()
    for role, source in sources.items():
        parent = source.with_name(f"{role}.parent.md")
        if parent.is_file():
            assert targets[role] == parent.relative_to(plugin).as_posix()
        else:
            declared = read_triggers.parent_contract_targets(role, plugin)
            assert len(declared) == 1
            assert targets[role] == declared[0].path.relative_to(plugin).as_posix()


@pytest.mark.parametrize("role,relative,title,level", subject.RETURN_TARGETS)
def test_return_contract_sync_tracks_output_only(tmp_path, monkeypatch, role, relative, title, level) -> None:
    """正本の固有欄だけが追随し、内部手順を混入せず、再生成で差分が増えない。"""
    monkeypatch.setattr(subject, "RETURN_TARGETS", ((role, relative, title, level),))
    plugin = tmp_path / "agent-toolkit"
    source = plugin / f"share/{role}.subagent.md"
    target = plugin / relative
    source.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    (source.parent / "rules-subagent.md").write_text("# 共通契約\n", encoding="utf-8")
    output = (
        "```text\n固有欄: 旧値\n## 逐語の見出し\n```\n\n"
        "### 解釈\n\n`rules-subagent.md`と[共通契約](rules-subagent.md#共通契約)を参照する。\n"
    )
    source.write_text(
        "# 役割\n\n## 内部手順\n\n非公開の手順。\n\n## 出力\n\n" + output + "\n## 内部の後処理\n\n内部記録。\n",
        encoding="utf-8",
    )
    target.write_text(
        f"# 呼出側\n\n<!-- return-contract:{role}:start -->\n旧契約\n<!-- return-contract:{role}:end -->\n",
        encoding="utf-8",
    )
    subject.sync_return_contracts(tmp_path)
    first = target.read_bytes()
    assert "固有欄: 旧値" in first.decode()
    assert "非公開の手順" not in first.decode()
    assert "内部準備" not in first.decode()
    assert "内部記録" not in first.decode()
    assert "## 逐語の見出し" in first.decode()
    assert "#" * (level + 1) + " 解釈" in first.decode()
    reference = subject.relocate_path("rules-subagent.md#共通契約", source, target)
    assert f"[共通契約]({reference})" in first.decode()
    subject.sync_return_contracts(tmp_path)
    assert target.read_bytes() == first
    source.write_text(
        source.read_text(encoding="utf-8").replace("固有欄: 旧値", "固有欄: 改訂値\n追加固有欄: 追加値"), encoding="utf-8"
    )
    subject.sync_return_contracts(tmp_path)
    assert "固有欄: 改訂値" in target.read_text(encoding="utf-8")
    assert "追加固有欄: 追加値" in target.read_text(encoding="utf-8")
    after_output = target.read_bytes()
    source.write_text(source.read_text(encoding="utf-8").replace("非公開の手順", "改訂した内部手順"), encoding="utf-8")
    subject.sync_return_contracts(tmp_path)
    assert target.read_bytes() == after_output


def test_return_sync_invalid_boundary_does_not_write_any_target(tmp_path, monkeypatch) -> None:
    """後半の生成境界が壊れても、先行文書を途中まで書き換えない。"""
    targets = subject.RETURN_TARGETS[:2]
    monkeypatch.setattr(subject, "RETURN_TARGETS", targets)
    for role, relative, _title, _level in targets:
        source = tmp_path / f"agent-toolkit/share/{role}.subagent.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("## 出力\n返却形式。\n", encoding="utf-8")
        target = tmp_path / "agent-toolkit" / relative
        target.write_text(f"<!-- return-contract:{role}:start -->\n<!-- return-contract:{role}:end -->", encoding="utf-8")
    last = tmp_path / "agent-toolkit" / targets[-1][1]
    last.write_text("境界なし", encoding="utf-8")
    first = tmp_path / "agent-toolkit" / targets[0][1]
    before = first.read_bytes()
    with pytest.raises(ValueError):
        subject.sync_return_contracts(tmp_path)
    assert first.read_bytes() == before


def test_real_return_contracts_generate_in_isolated_fixture(tmp_path) -> None:
    """実在する全正本と配置先を隔離し、実行レビューの内部処理と相対参照も確認する。"""
    files = set()
    for role, relative, _title, _level in subject.RETURN_TARGETS:
        files.add(f"agent-toolkit/share/{role}.subagent.md")
        files.add("agent-toolkit/" + relative)
    files |= {
        "agent-toolkit/share/rules-subagent.md",
        "agent-toolkit/share/exec-review-recording.md",
    }
    for relative in files:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((subject.REPO_ROOT / relative).read_bytes())
    subject.sync_return_contracts(tmp_path)
    first = {relative: (tmp_path / relative).read_bytes() for relative in files}
    subject.sync_return_contracts(tmp_path)
    assert first == {relative: (tmp_path / relative).read_bytes() for relative in files}
    review = (tmp_path / "agent-toolkit/share/exec-review.parent.md").read_text(encoding="utf-8")
    block = review.split("<!-- return-contract:exec-review:start -->")[1]
    assert "レビューHEAD:" in block and "読者別探索の結果申告" in block
    assert "判定工程: 公開工程" in block
    assert "返却する前に、以下の処理" not in block
    assert "review-table validate" not in block
    assert "fork_turns" not in block
    for role, relative, required, internal in (
        ("external-write-review", "skills/external-write-review/SKILL.md", "3観点それぞれ", "## 観点"),
        ("bulk-replace-review", "skills/delegation/references/bulk-replacement.md", "読んだ変更箇所数:", "## 観点"),
        ("usability-review", "share/usability-review.parent.md", "試行: <手段>", "## 操作"),
        ("session-termination", "share/session-termination.parent.md", "延期後にadoptしたAWI:", "## 生成物とpush"),
    ):
        text = (tmp_path / "agent-toolkit" / relative).read_text(encoding="utf-8")
        generated = text.split(f"<!-- return-contract:{role}:start -->")[1].split(f"<!-- return-contract:{role}:end -->")[0]
        assert required in generated
        assert internal not in generated
        assert "required_inputs:" not in generated
        assert "作業の開始時に" not in generated
    source = tmp_path / "agent-toolkit/share/exec-review.subagent.md"
    target = tmp_path / "agent-toolkit/share/exec-review.parent.md"
    original = source.read_text(encoding="utf-8")
    added = original.replace("## 出力\n", "## 出力\n\n追加固有欄: 節先頭の返却値\n", 1)
    source.write_text(added, encoding="utf-8")
    subject.sync_return_contracts(tmp_path)
    after_addition = target.read_bytes()
    assert "追加固有欄: 節先頭の返却値" in after_addition.decode()
    # 内部手順の境界は見出しで定まり、準備本文の改訂や追加で生成結果は変わらない。
    changed_preparation = added.replace("返却する前に、以下の処理を完了させる。", "内部準備の改訂文。")
    changed_preparation = changed_preparation.replace("## 返却前の処理\n", "## 返却前の処理\n\n追加した内部手順。\n")
    source.write_text(changed_preparation, encoding="utf-8")
    subject.sync_return_contracts(tmp_path)
    assert target.read_bytes() == after_addition
    # 旧抽出アンカーだった通常の文面を変えても、外部契約全体の同期が成立する。
    changed_output = changed_preparation.replace("読者別探索の結果申告は", "読者別探索の申告形式は")
    changed_output = changed_output.replace("返却の状態行、", "返却する状態行、")
    changed_output = changed_output.replace("延期用の記録によって許容された", "延期用の記録で認めた")
    source.write_text(changed_output, encoding="utf-8")
    subject.sync_return_contracts(tmp_path)
    updated = target.read_text(encoding="utf-8")
    assert "追加固有欄: 節先頭の返却値" in updated
    assert "読者別探索の申告形式は" in updated
    assert "返却する状態行、" in updated
    assert "内部準備の改訂文" not in updated
    assert "追加した内部手順" not in updated
    after_wording_change = target.read_bytes()
    subject.sync_return_contracts(tmp_path)
    assert target.read_bytes() == after_wording_change
    refined = (tmp_path / "agent-toolkit/skills/refine-prompt/SKILL.md").read_text(encoding="utf-8")
    assert "`../../share/rules-subagent.md`" in refined
