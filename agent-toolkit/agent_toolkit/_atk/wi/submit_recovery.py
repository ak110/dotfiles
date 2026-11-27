from __future__ import annotations

import datetime
import pathlib
import socket
from typing import Any

from agent_toolkit._atk.wi import add, frontmatter, layout, sync


def save(notes: pathlib.Path, run: dict[str, Any], path: pathlib.Path) -> None:
    if run.get("status") != "failed":
        return
    proposal = f"wi-submit-recovery@{run['run_id']}"
    with sync.repo_lock(notes, timeout=30):
        for entry in layout.iter_entry_paths(notes):
            parsed = frontmatter.parse_frontmatter(entry.read_text(encoding="utf-8"))
            if parsed and parsed[0].get("proposal_id") == proposal:
                return
    identifier = run["request_ids"][0]
    body = (
        f"---\nproposal_id: {proposal}\n---\n\n"
        "# 独立WI投入の未完了受付を再開する\n\n"
        "独立WI投入が失敗したため、保存済みの処置を保って未完了の要求を完遂する。\n\n"
        "## 反映内容と反映先\n\n"
        f"受付を保持するホスト`{socket.gethostname()}`で`atk wi submit --show {identifier}`を実行する。\n"
        f"実行`{run['run_id']}`、受付`{', '.join(run['request_ids'])}`の全件を対象にする。\n"
        f"記録: `{path}`。\n"
        "`saved_wi_bindings`と結果を先に読み、保存済みWIを重ねて投入しない。\n"
        f"`atk wi submit --resume {identifier}`で同じ実行を再開し、完了と報告の同期を確かめる。\n"
        "確認UWIが未回答なら、その回答と解除条件を満たしてから再開する。\n"
        "元会話や残時間が不足する場合の引き継ぎは、"
        "`agent-toolkit:atk-mode/playbooks/wi-submission.md`「失敗・再開・保持」に従う。\n\n"
        "## 完成条件\n\n"
        "受付の全要求の処置と報告の同期が完了するか、未完了の要求と保存済みWIを"
        "引き継いだ担当・再開時機が確定し、ユーザー固有の判断だけが確認UWIに残っている。\n"
    )
    dependencies = tuple(
        filename
        for item in (run.get("result") or {}).get("results", [])
        if item["status"] == "incomplete"
        for filename in item["files"]
    )
    add.add_entries(
        notes,
        messages=[body],
        target_repo=run["target_repo"],
        source="agent",
        now=datetime.datetime.now(datetime.UTC),
        depends_on=tuple(dict.fromkeys(dependencies)),
        storage=sync.Storage(local_only=True, lock_timeout=30),
    )
