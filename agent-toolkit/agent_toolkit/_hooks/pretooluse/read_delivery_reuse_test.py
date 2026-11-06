"""同一の資料・宣言・保存状態で、配送と復元の意味と導出仕事量を比較する。"""

import copy
import dataclasses
import functools
import json
import pathlib
import time

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
    descriptor = vars(read_triggers.ReadTrigger)["targets"]
    derive = descriptor.func if isinstance(descriptor, functools.cached_property) else descriptor.fget
    assert derive is not None
    original_resolve = pathlib.Path.resolve
    observations = []

    def observe(cached):
        state = {rules_context.OPERATION_SKILL_READY_KEY: {"unrelated": ["other"]}}
        rows = [
            read_triggers.ReadTrigger(source, index + 1, "操作", ("tool:Do",), paths, ("対象", "", "対象"))
            for index in range(3)
        ]
        counts = {"targets": 0, "resolve": 0}

        def counted_targets(row):
            counts["targets"] += 1
            return derive(row)

        def resolve(path, *args, **kwargs):
            counts["resolve"] += 1
            return original_resolve(path, *args, **kwargs)

        def update(_session, mutate):
            mutate(state)

        with monkeypatch.context() as patch:
            if not cached:
                patch.setattr(read_triggers.ReadTrigger, "targets", property(counted_targets))
            patch.setattr(pathlib.Path, "resolve", resolve)
            patch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
            patch.setattr(read_delivery, "read_state", lambda _session: state)
            patch.setattr(read_delivery, "update_state", update)
            patch.setattr(notice, "read_state", lambda _session: state)
            patch.setattr(notice, "update_state", update)
            patch.setattr(read_delivery.delegated_session, "is_lightweight_delegated", lambda _env: False)
            notice.set_warning_session_id("")
            notice.begin_delivery()
            started = time.perf_counter()
            decisions = []
            for _ in range(3):
                notice.begin_delivery()
                decisions.append(dataclasses.asdict(read_delivery.deliver({"turn_id": "t"}, "Do", {}, "reuse")))
                selected = read_delivery.selected_targets(state[rules_context.OPERATION_SKILL_READY_KEY], "main", rows=rows)
                assert [(target.path, target.heading) for target in selected] == list(
                    zip(paths, ("対象", "", "対象"), strict=True)
                )
            before_other_consumer = counts["resolve"]
            # 案内以外の消費側が同じ行を反復参照する持ち込み例。
            rendered = [tuple(str(target) for target in row.targets) for row in rows for _ in range(4)]
            elapsed = time.perf_counter() - started
            if cached:
                assert counts["resolve"] == before_other_consumer
                counts["targets"] = sum("targets" in vars(row) for row in rows)
        return {"decisions": decisions, "state": copy.deepcopy(state), "rendered": rendered}, {**counts, "seconds": elapsed}

    for cached in (False, True, False, True):
        semantic, metrics = observe(cached)
        observations.append((semantic, metrics))
    assert all(semantic == observations[0][0] for semantic, _metrics in observations)
    before, after = observations[0][1], observations[1][1]
    assert after["targets"] == 3 < before["targets"]
    assert after["resolve"] < before["resolve"]
    print(json.dumps([metrics for _semantic, metrics in observations], ensure_ascii=False))


def test_one_hook_call_parses_declarations_once_and_next_call_detects_revision(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """宣言一覧と取得済み資料の再解決が同じ要求で解析を共有し、次の要求では改訂後の宣言を読む。"""
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

    reads = []
    original_read_text = pathlib.Path.read_text

    def counted_read_text(path, *args, **kwargs):
        if path == owner:
            reads.append(path)
        return original_read_text(path, *args, **kwargs)

    observed: list[list[str]] = []

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

    monkeypatch.setattr(pathlib.Path, "read_text", counted_read_text)
    monkeypatch.setattr(read_delivery, "declarations", declarations)
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
    assert len(reads) == 1

    write_owner("改訂後")
    notice.begin_delivery()
    read_delivery.deliver({"turn_id": "t"}, "Do", {}, "reuse")
    assert len(observed) > first
    assert all(conditions == ["改訂後", "改訂後"] for conditions in observed[first:])
    assert len(reads) == 2
