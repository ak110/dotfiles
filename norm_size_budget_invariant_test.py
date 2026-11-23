"""エージェントへ届ける規範のMarkdownの合計サイズが、上限を超えないことを確かめる。

対象は`agent-toolkit/skills/`と`agent-basics/skills/`の追跡済みMarkdown（試験用の`fixtures/`を除く）で、
両プラグインにまたがるためリポジトリ直下に置く。
規範を足す前に引く原則（`agent-basics:writing-standards/references/quality-principles-subtraction.md`）を文章だけで
守れず合計が増え続けたため、上限を超えたら次のセッションで整理できるよう、発見した担当に削減AWIの登録を案内する。
"""

import pathlib
import re
import subprocess

_REPO_ROOT = pathlib.Path(__file__).resolve().parent
_ROOTS = ("agent-toolkit/skills", "agent-basics/skills")


_BUDGET_BYTES = 2_760_000


def _norm_markdown_bytes(root: pathlib.Path) -> int:
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", *(f"{prefix}/*.md" for prefix in _ROOTS)],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    paths = [path for path in listed.split("\0") if path and "/fixtures/" not in path]
    return sum((root / path).stat().st_size for path in paths if (root / path).is_file())


def test_norm_markdown_within_budget() -> None:
    total = _norm_markdown_bytes(_REPO_ROOT)
    assert total <= _BUDGET_BYTES, (
        f"規範のMarkdownの合計が{total}バイトで、上限{_BUDGET_BYTES}バイトを{total - _BUDGET_BYTES}バイト超えた。"
        "次のセッションで削減するため、発見した担当が削減AWIを登録する。"
        ".claude/skills/edit-dotfiles-norms/SKILL.mdの「規範を削除・縮小するときの消失確認」に従い、"
        "削減量と整理後の上限は、必要な規範を保ちながらdotfilesの価値を最大化するように決める。"
    )


_ATK_MODE = _REPO_ROOT / "agent-toolkit/skills/atk-mode"
_ALL_REPOS_DOCS = (
    "playbooks/process-wi-all-repos.md",
    "playbooks/process-wi-all-repos-results.md",
    "roles/repository-process.subagent.md",
)
_EXECUTION_DOC = "playbooks/process-wi-execution.md"
_RESULT_FILE_TOKENS = ("result_file", "結果JSON", "writer_sessions")
_DUAL_RECORD = re.compile(r"(引き継ぎ記録|進捗ログ)(と|および|及び|、)(既存の|計画の)?(引き継ぎ記録|進捗ログ)")
_SUBSECOND_TERMS = ("秒以下", "開始・終了", "開始と終了")
_LEGACY_SCOPES = ("旧形式", "計画が無い", "計画なし")


def _section(text: str, heading: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"(#+) (.+)$", line)
        if match and match.group(2).strip() == heading:
            level = len(match.group(1))
            body: list[str] = []
            for rest in lines[index + 1 :]:
                next_match = re.match(r"(#+) ", rest)
                if next_match and len(next_match.group(1)) <= level:
                    break
                body.append(rest)
            return "\n".join(body)
    return ""


def _numbered_step(section: str, number: int) -> str:
    match = re.search(rf"^{number}\. .*?(?=^{number + 1}\. |\Z)", section, re.MULTILINE | re.DOTALL)
    return match.group(0) if match else ""


def _result_file_violations(text: str) -> list[str]:
    return [token for token in _RESULT_FILE_TOKENS if token in text]


def _dual_verification_record_violations(text: str) -> list[str]:
    step = _numbered_step(_section(text, "継続する工程"), 2)
    sentences = re.split(r"[。\n]", step)
    return [s for s in sentences if "検証結果" in s and (_DUAL_RECORD.search(s) or "両方" in s)]


def _handwritten_timing_violations(text: str) -> list[str]:
    section = _section(text, "工程の計測と統合準備")
    violations = []
    for sentence in re.split(r"[。\n]", section):
        if "結果・特記事項" not in sentence or not any(term in sentence for term in _SUBSECOND_TERMS):
            continue
        limited = ("だけ" in sentence or "のみ" in sentence) and any(scope in sentence for scope in _LEGACY_SCOPES)
        if not limited:
            violations.append(sentence)
    return violations


def _read(relative: str) -> str:
    return (_ATK_MODE / relative).read_text(encoding="utf-8")


def test_all_repos_has_no_unread_result_file() -> None:
    for relative in _ALL_REPOS_DOCS:
        assert not _result_file_violations(_read(relative)), (
            f"{relative}に読み手のいない結果ファイルの書込要求が戻った。対象別の結果は完了報告へ書く。"
        )


def test_execution_records_verification_once() -> None:
    text = _read(_EXECUTION_DOC)
    assert _numbered_step(_section(text, "継続する工程"), 2), "process-wi-executionの手順2が見つからない。"
    assert not _dual_verification_record_violations(text), (
        "手順2で検証結果を引き継ぎ記録と進捗ログへ重ねて記録する要求が戻った。記録先は1か所にする。"
    )


def test_execution_timing_has_no_handwritten_subsecond_times() -> None:
    text = _read(_EXECUTION_DOC)
    assert _section(text, "工程の計測と統合準備"), "process-wi-executionの工程計測の節が見つからない。"
    assert not _handwritten_timing_violations(text), (
        "新形式の計画の結果・特記事項欄へ秒以下の開始・終了を手書きする要求が戻った。日時セルの時刻を使う。"
    )


def test_invariants_reject_reintroduced_records() -> None:
    results = _read("playbooks/process-wi-all-repos-results.md")
    assert _result_file_violations(results + "\n結果を`result_file`へUTF-8のJSONで保存する。\n")

    execution = _read(_EXECUTION_DOC)
    old_step = "の1か所へ記録し"
    assert old_step in execution
    dual = execution.replace(
        "計画の進捗ログ（計画が無ければ引き継ぎ記録）の1か所へ記録し", "既存の引き継ぎ記録と進捗ログへ記録し", 1
    )
    assert _dual_verification_record_violations(dual)

    bullet = "- 統合準備完了、統合許可の受領"
    assert bullet in execution
    handwritten = execution.replace(
        bullet, "- 秒以下を持つ時差付きの開始・終了と観測元を`結果・特記事項`欄へ保存する。\n" + bullet, 1
    )
    assert _handwritten_timing_violations(handwritten)


def test_invariants_accept_legitimate_time_records() -> None:
    legacy = (
        "## 工程の計測と統合準備\n\n"
        "- 日時セルが分の精度の旧形式の計画と計画が無い引き継ぎ記録だけ、"
        "秒以下の時差付きの開始・終了と観測元を`結果・特記事項`欄へ書く。\n"
        "- 計画の進捗ログへ検証の開始と終了を記録する。\n"
    )
    assert not _handwritten_timing_violations(legacy)
    single = "## 継続する工程\n\n2. 実装: `検証結果`は計画の進捗ログ（計画が無ければ引き継ぎ記録）の1か所へ記録する。\n3. 次\n"
    assert not _dual_verification_record_violations(single)
