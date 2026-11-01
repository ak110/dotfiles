"""スキル選択の提出物を、初期選択・追加選択の漏れ、適用外の余剰、未知の識別子に分けて採点する。

期待集合は公開素材`skills.json`の`description`・`body`・`references`の適用条件と依頼文から導いた（本課題の版）。
`initial`は依頼と`description`だけで選べる必須スキル、`additional`は選んだスキルの本文か、条件が当てはまる
参照資料にだけ書かれた条件で必要になる必須スキルである。追加選択の検査は`additional`へ提出したかを見るため、
同じ集合を全て`initial`へ入れた提出物（本文を読まずに選んだ形）とも区別できる。
`allowed`は選んでも選ばなくても誤りにしない境界の候補で、`excluded`は既知の識別子から必須と境界の候補を除いた集合である。
"""

import pathlib
import runpy
import sys

KNOWN = {
    "research-standards",
    "design-review",
    "source-dating",
    "writing-quality",
    "run-checks",
    "config-consumers",
    "bug-analysis",
    "incident-history",
    "glossary",
    "publish-release",
    "process-queue",
    "search-files",
}
# 依頼IDごとに（初期選択の必須集合、追加選択の必須集合、境界の候補）。
EXPECTED = {
    # 外部記事を根拠に改善案を評価する時のsource-datingは、research-standardsの本文にだけある。
    "article-eval": ({"research-standards", "design-review"}, {"source-dating"}, {"search-files"}),
    "paraphrase": ({"research-standards", "design-review"}, {"source-dating"}, {"search-files"}),
    # 設定ファイルを修正した時のconfig-consumersは、writing-qualityの本文にだけある。
    "edit-and-check": ({"writing-quality", "run-checks"}, {"config-consumers"}, {"search-files"}),
    # 対策を提案する時のincident-historyは、bug-analysisの本文にだけある。
    # 対策の説明は案の採否の判定を求めていないため、design-reviewは境界の候補とする。
    "bug-explain": ({"bug-analysis"}, {"incident-history"}, {"search-files", "research-standards", "design-review"}),
    "known-fact": (set(), set(), {"research-standards"}),
    # 社内向けの文書を新しく作る時のglossaryは、writing-qualityの参照資料にだけある。
    # 改善案の評価ではないためsource-datingは、新規作成で既存ファイルを修正しないためrun-checksは境界の候補とする。
    "boundary": (
        {"research-standards", "writing-quality"},
        {"glossary"},
        {"search-files", "run-checks", "source-dating"},
    ),
}


def _selection(task, request_id):
    result = task.select(request_id)
    return set(result["initial"]), set(result["additional"])


def _chosen(task, request_id):
    initial, additional = _selection(task, request_id)
    return initial | additional


def _checks():
    for request_id, (initial, additional, allowed) in EXPECTED.items():
        excluded = KNOWN - initial - additional - allowed
        yield f"{request_id}-initial", lambda task, r=request_id, s=initial: s <= _selection(task, r)[0], True
        yield f"{request_id}-additional", lambda task, r=request_id, s=additional: s <= _selection(task, r)[1], True
        yield f"{request_id}-excluded", lambda task, r=request_id, s=excluded: not _chosen(task, r) & s, True
        yield f"{request_id}-known", lambda task, r=request_id: _chosen(task, r) <= KNOWN, True


runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](sys.argv[1], list(_checks()))
