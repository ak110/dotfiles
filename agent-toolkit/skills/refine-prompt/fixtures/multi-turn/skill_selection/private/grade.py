"""スキル選択の提出物を、必須集合の漏れ・適用外集合の余剰・未知の識別子に分けて採点する。

期待集合は公開素材`skills.json`の`description`と`body`の適用条件から導いた。
`allowed`は選んでも選ばなくても誤りにしない境界の候補で、`excluded`は既知の識別子から必須と境界の候補を除いた集合である。
"""

import pathlib
import runpy
import sys

KNOWN = {
    "research-standards",
    "design-review",
    "writing-quality",
    "run-checks",
    "bug-analysis",
    "publish-release",
    "process-queue",
    "search-files",
}
EXPECTED = {
    # 外部記事を根拠に改善案を評価する条件は、research-standardsの本文にだけある。
    "article-eval": ({"research-standards", "design-review"}, {"search-files"}),
    "paraphrase": ({"research-standards", "design-review"}, {"search-files"}),
    # 既存ファイルの修正後のチェックは、writing-qualityの本文とrun-checksのdescriptionの両方から導ける。
    "edit-and-check": ({"writing-quality", "run-checks"}, {"search-files"}),
    # 再発防止策を提案する時のdesign-reviewは、bug-analysisの本文にだけある。
    "bug-explain": ({"bug-analysis", "design-review"}, {"search-files", "research-standards"}),
    "known-fact": (set(), {"research-standards"}),
    # 新しい文書の作成は既存ファイルの修正ではないため、run-checksは境界の候補とする。
    "boundary": ({"research-standards", "writing-quality"}, {"search-files", "run-checks"}),
}


def _chosen(task, request_id):
    result = task.select(request_id)
    return set(result["initial"]) | set(result["additional"])


def _checks():
    for request_id, (required, allowed) in EXPECTED.items():
        excluded = KNOWN - required - allowed
        yield f"{request_id}-required", lambda task, r=request_id, s=required: s <= _chosen(task, r), True
        yield f"{request_id}-excluded", lambda task, r=request_id, s=excluded: not _chosen(task, r) & s, True
        yield f"{request_id}-known", lambda task, r=request_id: _chosen(task, r) <= KNOWN, True


runner = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[4] / "scripts" / "multi_turn_trials.py"))
runner["grade_fixture"](sys.argv[1], list(_checks()))
