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
