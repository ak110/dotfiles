"""agent-toolkit参照の主題別不変条件を検証する。"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import warnings

import pytest
from agent_toolkit._testing.git_repository import commit_all, init_repository, run_git

from _test_helpers import _PLUGIN_PREFIX, _format_unresolved, _tracked_source_paths

_REFERENCE_BOUNDARY = r"(?<![A-Za-z0-9_:-])"


_SKILL_INVOCATION_PATTERN = re.compile(rf"{_REFERENCE_BOUNDARY}{_PLUGIN_PREFIX}:([A-Za-z0-9][A-Za-z0-9_-]*)")


_SKILL_PATH_PATTERN = re.compile(
    rf"{_REFERENCE_BOUNDARY}{_PLUGIN_PREFIX}/skills/[A-Za-z0-9][A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*/?"
)


_PYTHON_PATH_PATTERN = re.compile(
    rf"{_REFERENCE_BOUNDARY}{_PLUGIN_PREFIX}/(?:scripts|agent_toolkit)/"
    r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.py"
)


# 基準版の原文・由来・逆引きを固定引用として保持する正式データ。READMEと他の文書は検査する。
_VERBATIM_REFERENCE_SOURCES = frozenset(
    pathlib.Path(value)
    for value in (
        "docs/development/norm-restructure-20261022/ledger.tsv",
        "docs/development/norm-restructure-20261022/mapping.json",
        "docs/development/norm-restructure-20261022/reverse-index.tsv",
    )
)


_SOURCE_MANIFEST = pathlib.Path("docs/development/norm-restructure-20261022/source-manifest.json")


# 読込表の別ファイル（`*-delivery-<n>.md`、`*reading-references.md`）は本文の読込表へ統合して廃止した。
# 規範再構成の記録は作成時点の配置を保持するため、記録が指すこれらの参照には現在の実体を求めない。
_NORM_RESTRUCTURE_RECORDS = pathlib.Path("docs/development/norm-restructure-20261022")


_RETIRED_READING_TABLE_PATTERN = re.compile(r"(?:-delivery-\d+|reading-references)\.md$")


_INCIDENTS_RUNTIME = pathlib.Path("docs/development/incidents-hooks-202606-20260914.md")


_INCIDENTS_VALIDATION = pathlib.Path("docs/development/incidents-judgment-202608-20260901.md")


_INCIDENTS_WORKFLOWS = pathlib.Path("docs/development/incidents-stagnation-202607-20260906.md")


_INCIDENTS_REVIEW_SCOPE = pathlib.Path("docs/development/incidents-review-scope-20260901-20261003.md")


_CONCEPTS_CONFIRMATION_DECISIONS = pathlib.Path("docs/development/concepts-confirmation-decisions.md")
_CONCEPTS_CONFIRMATION_FORMAT = pathlib.Path("docs/development/concepts-confirmation-format.md")


_DESIGN_HOSTS_AUTHORIZATION = pathlib.Path("docs/development/design-hosts-authorization.md")


_NORM_RESTRUCTURE_README = pathlib.Path("docs/development/norm-restructure/README.md")


_NORM_RESTRUCTURE_CLAUSES_TEST = pathlib.Path("scripts/norm_restructure_clauses_test.py")


_SESSION_RECORDS = pathlib.Path("agent-toolkit/agent_toolkit/_atk/session_records.py")


_SESSION_RECORDS_TEST = pathlib.Path("agent-toolkit/agent_toolkit/_atk/session_records_test.py")


_ALLOWED_UNRESOLVED_REFERENCE_COUNTS = {
    # 保存済みの起動標識とscopeを読むための旧名。現行入口としては提供しない。
    (f"{_PLUGIN_PREFIX}:process-wi-all-repos", _SESSION_RECORDS): 2,
    (f"{_PLUGIN_PREFIX}:process-wi-all-repos", _SESSION_RECORDS_TEST): 1,
    (
        f"{_PLUGIN_PREFIX}:process-wi-all-repos",
        pathlib.Path("agent-toolkit/skills/review-session/scripts/session_evidence_candidates_users.py"),
    ): 1,
    (f"{_PLUGIN_PREFIX}:session-review-batch", pathlib.Path("agent-toolkit/agent_toolkit/_common/session_review.py")): 1,
    (f"{_PLUGIN_PREFIX}:session-review", pathlib.Path("agent-toolkit/agent_toolkit/_common/skill_modes.py")): 2,
    (f"{_PLUGIN_PREFIX}:session-review-batch", pathlib.Path("agent-toolkit/agent_toolkit/_common/skill_modes.py")): 1,
    (
        f"{_PLUGIN_PREFIX}:session-review",
        pathlib.Path("agent-toolkit/agent_toolkit/_atk/wi/run_skill_scope_invariant_test.py"),
    ): 2,
    (
        f"{_PLUGIN_PREFIX}:session-review-batch",
        pathlib.Path("agent-toolkit/agent_toolkit/_atk/wi/run_skill_scope_invariant_test.py"),
    ): 1,
    (
        f"{_PLUGIN_PREFIX}:session-review",
        pathlib.Path("agent-toolkit/skills/user-confirmation-and-report/references/answer-transitions.md"),
    ): 2,
    (
        f"{_PLUGIN_PREFIX}:session-review-batch",
        pathlib.Path("agent-toolkit/skills/user-confirmation-and-report/references/answer-transitions.md"),
    ): 1,
    (f"{_PLUGIN_PREFIX}:agent-standards", _INCIDENTS_VALIDATION): 1,
    (f"{_PLUGIN_PREFIX}:feedback-standards", _INCIDENTS_WORKFLOWS): 1,
    (f"{_PLUGIN_PREFIX}:process-feedbacks", _INCIDENTS_VALIDATION): 1,
    (f"{_PLUGIN_PREFIX}:process-feedbacks", _INCIDENTS_WORKFLOWS): 4,
    (f"{_PLUGIN_PREFIX}:reviewee-standards", _INCIDENTS_REVIEW_SCOPE): 1,
    (f"{_PLUGIN_PREFIX}:shell-exec", _INCIDENTS_WORKFLOWS): 1,
    (f"{_PLUGIN_PREFIX}/scripts/hook.py", _INCIDENTS_RUNTIME): 1,
    (f"{_PLUGIN_PREFIX}:exit-session", _INCIDENTS_RUNTIME): 5,
    (f"{_PLUGIN_PREFIX}:exit-session", pathlib.Path("docs/development/audit-records-session-records.md")): 1,
    (f"{_PLUGIN_PREFIX}:exit-session", _SESSION_RECORDS): 1,
    (f"{_PLUGIN_PREFIX}:exit-session", _SESSION_RECORDS_TEST): 2,
    # 分割前のStop判定モジュールを当時の所在で記す障害事例
    (f"{_PLUGIN_PREFIX}/agent_toolkit/_hooks/stop_gate.py", _INCIDENTS_WORKFLOWS): 1,
    # 廃止したスキルの経緯を記す方針・設計の記録
    (f"{_PLUGIN_PREFIX}:realign-with-user", _CONCEPTS_CONFIRMATION_DECISIONS): 1,
    (f"{_PLUGIN_PREFIX}:realign-with-user", _CONCEPTS_CONFIRMATION_FORMAT): 1,
    (f"{_PLUGIN_PREFIX}:realign-with-user", _DESIGN_HOSTS_AUTHORIZATION): 2,
    # 判定台帳の説明が、基準commitの後に撤去したファイルを例として挙げる
    (f"{_PLUGIN_PREFIX}/skills/realign-with-user/SKILL.md", _NORM_RESTRUCTURE_README): 1,
    # 切り出しスクリプトのテストが一時リポジトリへ作成するスキルのパス
    (f"{_PLUGIN_PREFIX}/skills/demo/SKILL.md", _NORM_RESTRUCTURE_CLAUSES_TEST): 1,
}


def _codex_snapshot_path(source: pathlib.Path) -> pathlib.Path | None:
    """agent-toolkit配下の原本に対応するCodex生成snapshotのパスを返す。"""
    if not source.parts or source.parts[0] != _PLUGIN_PREFIX:
        return None
    return pathlib.Path(f"{_PLUGIN_PREFIX}-codex", *source.parts[1:])


def _allowed_unresolved_reference_counts(
    sources: list[pathlib.Path],
) -> dict[tuple[str, pathlib.Path], int]:
    """検査対象に実在する原本とCodex生成snapshotの既知参照数を返す。"""
    source_set = set(sources)
    allowed_counts: dict[tuple[str, pathlib.Path], int] = {}
    for (reference, source), count in _ALLOWED_UNRESOLVED_REFERENCE_COUNTS.items():
        candidates = (source, _codex_snapshot_path(source))
        for candidate in candidates:
            if candidate is not None and candidate in source_set:
                allowed_counts[(reference, candidate)] = count
    return allowed_counts


def _reference_exists(root: pathlib.Path, reference: str) -> bool:
    """起動名またはリポジトリ相対パスが実体へ解決する場合に真を返す。"""
    invocation_prefix = f"{_PLUGIN_PREFIX}:"
    if reference.startswith(invocation_prefix):
        skill_name = reference.removeprefix(invocation_prefix)
        return (root / _PLUGIN_PREFIX / "skills" / skill_name / "SKILL.md").is_file()
    return (root / reference).exists()


def _unresolved_references(root: pathlib.Path, sources: list[pathlib.Path]) -> list[tuple[str, pathlib.Path]]:
    """実体へ解決できない参照と不足した既知の事故記録参照を返す。"""
    allowed_counts = _allowed_unresolved_reference_counts(sources)
    captured_versions = _captured_reference_versions(root, sources)
    unverified_capture_versions: set[str] = set()
    unresolved: list[tuple[str, pathlib.Path]] = []
    for reference, source in _collect_references(root, sources):
        if _reference_exists(root, reference):
            continue
        if source.is_relative_to(_NORM_RESTRUCTURE_RECORDS) and _RETIRED_READING_TABLE_PATTERN.search(reference):
            continue
        # 再構成時点の固定台帳では、品質・技術・検索の移設前の公開名と配置を保持する。
        retired = (
            "bugfix",
            "check-execution",
            "review-standards",
            "writing-standards",
            "gitlab-ci-usage",
            "pytilpack-usage",
            "search",
            "secret-files",
            "single-lane-process",
            "process-wi-all-repos",
            "session-review-batch",
            "completion-report",
            "external-write-review",
            "plan-mode",
            "session-review",
        )
        if source.is_relative_to(_NORM_RESTRUCTURE_RECORDS) and any(
            reference == f"{_PLUGIN_PREFIX}:{skill}" or reference.startswith(f"{_PLUGIN_PREFIX}/skills/{skill}/")
            for skill in retired
        ):
            continue
        key = (reference, source)
        if captured_versions.get(key):
            captured_version = captured_versions[key].pop()
            captured = subprocess.run(
                ["git", "cat-file", "-e", f"{captured_version}:{reference}"],
                cwd=root,
                check=False,
                capture_output=True,
            )
            if captured.returncode == 0:
                continue
            captured_commit = run_git(root, "cat-file", "-e", f"{captured_version}^{{commit}}", check=False)
            if captured_commit.returncode != 0:
                shallow = run_git(root, "rev-parse", "--is-shallow-repository", check=False)
                if shallow.returncode == 0 and shallow.stdout.strip() == "true":
                    if captured_version not in unverified_capture_versions:
                        warnings.warn(
                            f"{_SOURCE_MANIFEST}: 浅いcheckoutに取得版{captured_version}が無いため、"
                            "sourcesの当時版の実体照合は未実施。現在参照の検査は継続する。",
                            RuntimeWarning,
                            stacklevel=2,
                        )
                        unverified_capture_versions.add(captured_version)
                    continue
        if allowed_counts.get(key, 0) > 0:
            allowed_counts[key] -= 1
            continue
        unresolved.append(key)
    for key, remaining_count in allowed_counts.items():
        unresolved.extend([key] * remaining_count)
    return unresolved


def _captured_reference_versions(root: pathlib.Path, sources: list[pathlib.Path]) -> dict[tuple[str, pathlib.Path], list[str]]:
    """固定manifestの取得ソースだけを、その記録が宣言した取得版へ対応付ける。

    READMEや任意のJSON、manifest内でも取得ソース一覧にない参照は現在の実体で検査する。
    有効な取得版とSHA-256を持つinventoryの各キー1回と、取得版の変更一覧（`implementation_changes_from_v5`）で
    変更後のSHA-256を持つ各項目の`path`1回を歴史参照にする。
    同じ値がそれ以外のfieldにも現れる場合は、その追加参照を現在の実体で検査する。
    呼出側は取得版のGitオブジェクトを照合し、浅いcheckoutの取得版不在は未検収と報告する。
    """
    if _SOURCE_MANIFEST not in sources:
        return {}
    manifest = json.loads((root / _SOURCE_MANIFEST).read_text(encoding="utf-8"))
    captured_head = manifest.get("head_at_capture")
    captured_sources = manifest.get("sources")
    if (
        not isinstance(captured_head, str)
        or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", captured_head) is None
        or not isinstance(captured_sources, dict)
    ):
        return {}
    captured: dict[tuple[str, pathlib.Path], list[str]] = {}
    for reference, metadata in captured_sources.items():
        if (
            isinstance(metadata, dict)
            and isinstance(metadata.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"]) is not None
        ):
            captured.setdefault((reference, _SOURCE_MANIFEST), []).append(captured_head)
    changes = manifest.get("implementation_changes_from_v5")
    for change in changes if isinstance(changes, list) else []:
        if (
            isinstance(change, dict)
            and isinstance(change.get("path"), str)
            and isinstance(change.get("after_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", change["after_sha256"]) is not None
        ):
            captured.setdefault((change["path"], _SOURCE_MANIFEST), []).append(captured_head)
    return captured


def test_agent_toolkit_references_resolve() -> None:
    """追跡中の`.json`・`.md`・`.py`にあるスキルとPython参照が全て実体へ解決する。"""
    root = pathlib.Path(__file__).resolve().parent
    sources = _tracked_source_paths(root)
    assert any(path.parts[0] == ".claude" for path in sources)
    unresolved = _unresolved_references(root, sources)
    assert not unresolved, _format_unresolved(unresolved)


def test_retired_reading_table_is_allowed_only_in_restructure_records(tmp_path: pathlib.Path) -> None:
    """廃止した読込表の別ファイルへの参照は、規範再構成の記録でだけ許し、現行文書では検出する。"""
    reference = f"{_PLUGIN_PREFIX}/skills/example/SKILL-delivery-1.md"
    record = _NORM_RESTRUCTURE_RECORDS / "public-compatibility.json"
    current = pathlib.Path(".claude/skills/example/SKILL.md")
    for relative in (record, current):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text(reference, encoding="utf-8")

    assert _unresolved_references(tmp_path, [record, current]) == [(reference, current)]


def test_existing_references_resolve(tmp_path: pathlib.Path) -> None:
    """実在する起動名と相対パスを受理する。"""
    skill = tmp_path / _PLUGIN_PREFIX / "skills" / "present"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: present\n---\n", encoding="utf-8")
    root_test = skill.parent / "contract_test.py"
    root_test.write_text("", encoding="utf-8")
    source = pathlib.Path("source.md")
    (tmp_path / source).write_text(
        f"{_PLUGIN_PREFIX}:present\n{_PLUGIN_PREFIX}/skills/present/SKILL.md\n{_PLUGIN_PREFIX}/skills/contract_test.py\n",
        encoding="utf-8",
    )

    assert not _unresolved_references(tmp_path, [source])

    root_test.unlink()
    assert _unresolved_references(tmp_path, [source]) == [(f"{_PLUGIN_PREFIX}/skills/contract_test.py", source)]


def test_duration_node_ids_are_not_current_skill_invocations(tmp_path: pathlib.Path) -> None:
    """所要記録の架空入力は案内と区別し、同じ文字列の現行文書は検査する。"""
    reference = f"{_PLUGIN_PREFIX}:fixture-only"
    record = pathlib.Path("agent-toolkit/agent_toolkit/_testing/test_durations.json")
    (tmp_path / record).parent.mkdir(parents=True)
    (tmp_path / record).write_text(json.dumps({f"example_test.py::test_legacy[{reference}]": 1.0}), encoding="utf-8")
    current = pathlib.Path("current.md")
    (tmp_path / current).write_text(reference, encoding="utf-8")

    assert _unresolved_references(tmp_path, [record, current]) == [(reference, current)]


def test_captured_source_manifest_resolves_only_recorded_files_at_capture_commit(tmp_path: pathlib.Path) -> None:
    """撤去済みの取得ソースは取得版で確かめ、未取得・架空・版不明の参照は欠損として残す。"""
    reference = f"{_PLUGIN_PREFIX}/agent_toolkit/captured_example.py"
    repository = init_repository(tmp_path / "repo", files={reference: "# 取得時の本文\n"})
    captured_head = commit_all(repository, "取得版")
    (repository / reference).unlink()
    manifest = repository / _SOURCE_MANIFEST
    manifest.parent.mkdir(parents=True)
    captured_sources = {reference: {"sha256": hashlib.sha256("# 取得時の本文\n".encode()).hexdigest()}}
    data = {"head_at_capture": captured_head, "sources": captured_sources}
    manifest.write_text(json.dumps(data), encoding="utf-8")
    current = pathlib.Path("current-notes.json")
    (repository / current).write_text(json.dumps({"path": reference}), encoding="utf-8")

    assert _unresolved_references(repository, [_SOURCE_MANIFEST, current]) == [(reference, current)]

    data["current_reference"] = reference
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert _unresolved_references(repository, [_SOURCE_MANIFEST]) == [(reference, _SOURCE_MANIFEST)]
    del data["current_reference"]

    recorded_hash = captured_sources[reference]["sha256"]
    captured_sources[reference]["sha256"] = "不正な取得hash"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert _unresolved_references(repository, [_SOURCE_MANIFEST]) == [(reference, _SOURCE_MANIFEST)]
    captured_sources[reference]["sha256"] = recorded_hash

    nonexistent = f"{_PLUGIN_PREFIX}/agent_toolkit/never_captured.py"
    captured_sources[nonexistent] = {"sha256": "a" * 64}
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert _unresolved_references(repository, [_SOURCE_MANIFEST]) == [(nonexistent, _SOURCE_MANIFEST)]

    data["head_at_capture"] = "0" * 40
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert _unresolved_references(repository, [_SOURCE_MANIFEST]) == [
        (reference, _SOURCE_MANIFEST),
        (nonexistent, _SOURCE_MANIFEST),
    ]


def test_captured_manifest_in_shallow_checkout_keeps_current_references_checked(tmp_path: pathlib.Path) -> None:
    """取得commitの無い浅いcheckoutは歴史照合の未実施を示し、現在参照の欠損を検出する。"""
    reference = f"{_PLUGIN_PREFIX}/agent_toolkit/captured_example.py"
    text = "# 取得時の本文\n"
    repository = init_repository(tmp_path / "origin", files={reference: text})
    captured_head = commit_all(repository, "取得版")
    (repository / reference).unlink()
    manifest = repository / _SOURCE_MANIFEST
    manifest.parent.mkdir(parents=True)
    data = {"head_at_capture": captured_head, "sources": {reference: {"sha256": hashlib.sha256(text.encode()).hexdigest()}}}
    manifest.write_text(json.dumps(data), encoding="utf-8")
    commit_all(repository, "保存済み取得inventoryを保持してsourceを撤去")
    shallow = tmp_path / "shallow"
    run_git(tmp_path, "clone", "--depth=1", repository.as_uri(), str(shallow))
    assert run_git(shallow, "cat-file", "-e", f"{captured_head}^{{commit}}", check=False).returncode != 0

    with pytest.warns(RuntimeWarning, match="当時版の実体照合は未実施"):
        assert not _unresolved_references(shallow, [_SOURCE_MANIFEST])

    data["current_reference"] = reference
    (shallow / _SOURCE_MANIFEST).write_text(json.dumps(data), encoding="utf-8")
    current = pathlib.Path("current-notes.json")
    (shallow / current).write_text(json.dumps({"path": reference}), encoding="utf-8")
    with pytest.warns(RuntimeWarning, match="当時版の実体照合は未実施"):
        assert _unresolved_references(shallow, [_SOURCE_MANIFEST, current]) == [
            (reference, _SOURCE_MANIFEST),
            (reference, current),
        ]


def test_codex_snapshot_requires_exact_known_legacy_references(tmp_path: pathlib.Path) -> None:
    """Codex生成snapshotでも原本と同数の既知の失効参照だけを受理する。"""
    source = _codex_snapshot_path(_SESSION_RECORDS)
    assert source is not None
    target = tmp_path / source
    target.parent.mkdir(parents=True)
    legacy = f"{_PLUGIN_PREFIX}:exit-session"
    startup = f"{_PLUGIN_PREFIX}:process-wi-all-repos\n" * 2
    target.write_text(startup + legacy, encoding="utf-8")

    assert not _unresolved_references(tmp_path, [source])

    target.write_text(f"{startup}{legacy}\n{legacy}\n", encoding="utf-8")
    assert _unresolved_references(tmp_path, [source]) == [(legacy, source)]


def test_python_reference_templates_and_globs_are_ignored(tmp_path: pathlib.Path) -> None:
    """Python参照の雛形とグロブを未解決の実体参照として扱わない。"""
    source = pathlib.Path("source.md")
    (tmp_path / source).write_text(
        "\n".join(
            [
                f"{_PLUGIN_PREFIX}/agent_toolkit/*.py",
                f"{_PLUGIN_PREFIX}/agent_toolkit/<name>.py",
                f"{_PLUGIN_PREFIX}/skills/*/scripts/",
            ]
        ),
        encoding="utf-8",
    )

    assert not _unresolved_references(tmp_path, [source])


def _known_legacy_references() -> dict[pathlib.Path, list[str]]:
    """分割後の事故記録へ保持する既知の失効参照を返す。"""
    return {
        _INCIDENTS_RUNTIME: [
            f"{_PLUGIN_PREFIX}/scripts/hook.py",
            *[f"{_PLUGIN_PREFIX}:exit-session"] * 5,
        ],
        _INCIDENTS_VALIDATION: [
            f"{_PLUGIN_PREFIX}:agent-standards",
            f"{_PLUGIN_PREFIX}:process-feedbacks",
        ],
        _INCIDENTS_WORKFLOWS: [
            f"{_PLUGIN_PREFIX}:feedback-standards",
            *[f"{_PLUGIN_PREFIX}:process-feedbacks"] * 4,
            f"{_PLUGIN_PREFIX}:shell-exec",
            f"{_PLUGIN_PREFIX}/agent_toolkit/_hooks/stop_gate.py",
        ],
        _INCIDENTS_REVIEW_SCOPE: [f"{_PLUGIN_PREFIX}:reviewee-standards"],
    }


def test_incident_history_requires_exact_known_legacy_references(tmp_path: pathlib.Path) -> None:
    """既知の失効参照を過不足なく保持し、各参照の不足と超過を報告する。"""
    by_source = _known_legacy_references()
    for source, references in by_source.items():
        target = tmp_path / source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(references), encoding="utf-8")
    sources = list(by_source)
    assert not _unresolved_references(tmp_path, sources)

    for source, references in by_source.items():
        target = tmp_path / source
        for legacy in dict.fromkeys(references):
            missing = references.copy()
            missing.remove(legacy)
            target.write_text("\n".join(missing), encoding="utf-8")
            assert _unresolved_references(tmp_path, sources) == [(legacy, source)]

            excessive = [*references, legacy]
            target.write_text("\n".join(excessive), encoding="utf-8")
            assert _unresolved_references(tmp_path, sources) == [(legacy, source)]
        target.write_text("\n".join(references), encoding="utf-8")


def test_incident_history_rejects_different_unresolved_references(tmp_path: pathlib.Path) -> None:
    """既知参照を別の失効起動名へ置換し、失効パスも加えた違反を報告する。"""
    by_source = _known_legacy_references()
    for current_source, current_references in by_source.items():
        target = tmp_path / current_source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(current_references), encoding="utf-8")
    source = _INCIDENTS_VALIDATION
    references = by_source[source].copy()
    replaced = references.pop(0)
    missing_invocation = f"{_PLUGIN_PREFIX}:missing"
    missing_path = f"{_PLUGIN_PREFIX}/skills/missing/SKILL.md"
    (tmp_path / source).write_text(
        "\n".join([missing_invocation, missing_path, *references]),
        encoding="utf-8",
    )

    unresolved = _unresolved_references(tmp_path, list(by_source))
    assert unresolved == [(missing_invocation, source), (missing_path, source), (replaced, source)]
    formatted = _format_unresolved(unresolved)
    assert missing_invocation in formatted
    assert missing_path in formatted
    assert replaced in formatted
    assert str(source) in formatted


def test_verbatim_ledger_references_preserve_current_document_checks(tmp_path: pathlib.Path) -> None:
    """正式データの固定引用を保持し、同階層のREADME・別JSON・現行文書の参照不良を全て検出する。"""
    historical = [
        pathlib.Path("docs/development/norm-restructure-20261022") / name
        for name in ("ledger.tsv", "mapping.json", "reverse-index.tsv")
    ]
    current = [
        pathlib.Path("docs/development/norm-restructure-20261022/README.md"),
        pathlib.Path("docs/development/norm-restructure-20261022/current-notes.json"),
        pathlib.Path(".claude/skills/example/SKILL.md"),
    ]
    references = [
        f"{_PLUGIN_PREFIX}:archived-example",
        f"{_PLUGIN_PREFIX}/skills/archived-example/SKILL.md",
        f"{_PLUGIN_PREFIX}/agent_toolkit/archived_example.py",
    ]
    for relative in [*historical, *current]:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(references), encoding="utf-8")

    unresolved = _unresolved_references(tmp_path, [*historical, *current])

    assert unresolved == [(reference, source) for source in current for reference in references]


def _collect_references(root: pathlib.Path, sources: list[pathlib.Path]) -> list[tuple[str, pathlib.Path]]:
    """指定した追跡ファイルからスキルの起動名と相対パス参照を収集する。"""
    references: list[tuple[str, pathlib.Path]] = []
    for relative in sources:
        if relative in _VERBATIM_REFERENCE_SOURCES:
            continue
        # キーは実行案内ではなく、旧入力や架空の起動名も含むpytest node IDである。
        if relative == pathlib.Path("agent-toolkit/agent_toolkit/_testing/test_durations.json"):
            continue
        content = (root / relative).read_text(encoding="utf-8")
        references.extend((match.group(), relative) for match in _SKILL_INVOCATION_PATTERN.finditer(content))
        references.extend((match.group().rstrip("/"), relative) for match in _SKILL_PATH_PATTERN.finditer(content))
        references.extend((match.group(), relative) for match in _PYTHON_PATH_PATTERN.finditer(content))
    return references
