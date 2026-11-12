"""同一の資料・宣言・保存状態で、再利用の有無による配送と復元の結果を比較する。"""

import copy
import dataclasses
import pathlib

import pytest

from agent_toolkit._common import read_triggers
from agent_toolkit._hooks import notice, rules_context
from agent_toolkit._hooks.pretooluse import read_delivery


def test_delivery_and_restoration_share_targets(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    paths = tuple(tmp_path / f"reference-{index}.md" for index in range(3))
    for path in paths:
        path.write_text("# 基準\n## 対象\n必要な内容\n", encoding="utf-8")
    source = tmp_path / "source.md"
    source.write_text("# 宣言\n", encoding="utf-8")
    observations = []

    def observe(cached):
        state = {rules_context.OPERATION_SKILL_READY_KEY: {"unrelated": ["other"]}}
        rows = [
            read_triggers.ReadTrigger(source, index + 1, "操作", ("tool:Do",), paths, ("対象", "", "対象"))
            for index in range(3)
        ]

        def uncached_targets(row):
            return tuple(
                read_triggers.ReadTarget(path, row.headings[index] if row.headings else "")
                for index, path in enumerate(row.documents)
            )

        def update(_session, mutate):
            mutate(state)

        with monkeypatch.context() as patch:
            if not cached:
                patch.setattr(read_triggers.ReadTrigger, "targets", property(uncached_targets))
            patch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
            patch.setattr(read_delivery, "read_state", lambda _session: state)
            patch.setattr(read_delivery, "update_state", update)
            patch.setattr(notice, "read_state", lambda _session: state)
            patch.setattr(notice, "update_state", update)
            patch.setattr(read_delivery.delegated_session, "is_lightweight_delegated", lambda _env: False)
            notice.set_warning_session_id("")
            notice.begin_delivery()
            decisions = []
            for _ in range(3):
                notice.begin_delivery()
                decisions.append(dataclasses.asdict(read_delivery.deliver({"turn_id": "t"}, "Do", {}, "reuse")))
                selected = read_delivery.selected_targets(state[rules_context.OPERATION_SKILL_READY_KEY], "main", rows=rows)
                assert [(target.path, target.heading) for target in selected] == list(
                    zip(paths, ("対象", "", "対象"), strict=True)
                )
            # 案内以外の消費側が同じ行を反復参照する持ち込み例。
            rendered = [tuple(str(target) for target in row.targets) for row in rows for _ in range(4)]
        return {"decisions": decisions, "state": copy.deepcopy(state), "rendered": rendered}

    for cached in (False, True):
        observations.append(observe(cached))
    assert observations[0] == observations[1]


def test_next_hook_call_detects_declaration_revision(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """宣言解析を要求内で共有し、次の要求では改訂後の宣言を読む。"""
    (tmp_path / "rule.md").write_text("# 資料\n## 対象\n判断基準\n", encoding="utf-8")
    owner = tmp_path / "SKILL.md"

    def write_owner(condition: str) -> None:
        owner.write_text(
            "# 本体\n\n"
            "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n"
            "| --- | --- | --- | --- |\n"
            f"| {condition} | `rule.md#対象` | 判断 | `read:*/rule.md` |\n",
            encoding="utf-8",
        )

    observed: list[list[str]] = []
    reads = 0
    original_read_text = pathlib.Path.read_text

    def read_text(path, *args, **kwargs):
        nonlocal reads
        if path == owner:
            reads += 1
        return original_read_text(path, *args, **kwargs)

    def declarations(*_args, **_kwargs):
        # 宣言一覧の解決と、取得済み資料の宣言の再解決（入れ子のscope）を同じhook呼出しで行う。
        listed = read_triggers.parse_tables(owner, tmp_path, strict=True)
        with read_triggers.resolution_scope():
            resolved = read_triggers.parse_tables(owner, tmp_path, strict=True)
        observed.append([row.condition for row in (*listed, *resolved)])
        return []

    state: dict = {}

    def update(_session, mutate):
        mutate(state)

    monkeypatch.setattr(read_delivery, "declarations", declarations)
    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    monkeypatch.setattr(read_delivery, "read_state", lambda _session: state)
    monkeypatch.setattr(read_delivery, "update_state", update)
    monkeypatch.setattr(notice, "read_state", lambda _session: state)
    monkeypatch.setattr(notice, "update_state", update)
    monkeypatch.setattr(read_delivery.delegated_session, "is_lightweight_delegated", lambda _env: False)
    notice.set_warning_session_id("")

    write_owner("改訂前")
    notice.begin_delivery()
    read_delivery.deliver({"turn_id": "t"}, "Do", {}, "reuse")
    first = len(observed)
    assert first >= 1
    assert all(conditions == ["改訂前", "改訂前"] for conditions in observed)
    # 要求内共有の性能契約を、次要求での改訂検出とは別に本文の読取回数で観測する。
    assert reads == 1

    write_owner("改訂後")
    notice.begin_delivery()
    read_delivery.deliver({"turn_id": "t"}, "Do", {}, "reuse")
    assert len(observed) > first
    assert all(conditions == ["改訂後", "改訂後"] for conditions in observed[first:])
    assert reads == 2
