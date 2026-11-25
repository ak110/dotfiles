from __future__ import annotations

import functools
import json
import os
import pathlib
import subprocess
import sys
import typing

import selection_contract
import yaml
from agent_toolkit._atk.environment import AGENT_ENVIRONMENT_VARIABLES
from agent_toolkit._atk.wi import frontmatter as _wi_frontmatter
from agent_toolkit._atk.wi.constants import WI_STATE_ADOPTED, WI_STATE_REJECTED
from agent_toolkit._common.json_lines import split_lines
from agent_toolkit._git import remote as _git_remote
from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._plan import selection as _selection
from check_selection_common import _FIX_CONTENT, _FIX_PATHS, _FIX_PRIVATE_NOTES, InputError, Violation
from check_selection_lanes import _resume_plan_id, derive_lane_order, derives_lane_order

_FIX_YAML = (
    "選定結果のYAML構文を直す。文字列の値を単一引用符で囲み、値の中の`'`は`''`と重ねて書き直してから、同じコマンドを再実行する"
)


_FIX_MODEL = (
    "`担当モデル`は`実装担当`か`実行レビュー担当`のキーごとに"
    "`<claude|codex|agy>:<model>/<effort>`か`opencode:<provider>/<model>@<effort>`の値へ直す"
)


_NEXT_SOURCE_MISMATCH = "一致しない欄を、元のWIのfrontmatterの`depends_on`・`target_repo`の値へ直す"


def _check_allocation_sources(selection: dict[str, object], private_notes: pathlib.Path) -> list[Violation]:
    data = typing.cast(dict[str, typing.Any], selection[_selection.INITIAL_ALLOCATION_KEY])
    items = {
        item[_selection.WI_KEY]: item for item in typing.cast(list[dict[str, typing.Any]], _selection.decisions(selection))
    }
    writes, stages = selection_contract.current_write_stages(selection)
    sources: dict[str, dict[str, typing.Any]] = {}
    errors: list[Violation] = []
    for group in data["不可分成分"]:
        for edge in group["結合条件"]:
            first, second, kind, evidence = (edge[key] for key in ("WI1", "WI2", "種別", "根拠"))

            if (
                kind == "書込重複"
                or first not in items
                or second not in items
                or selection_contract.allocation_edge_failures(edge, writes, stages)
            ):
                continue
            mismatches: list[str] = []
            if kind == "依存":
                for name in (first, second):
                    if name not in sources:
                        try:
                            source = _plan_file.find_wi_source(name, private_notes)
                            parsed = None
                            if source is not None:
                                parsed = _wi_frontmatter.parse_frontmatter(source.read_text(encoding="utf-8"))
                        except (OSError, UnicodeDecodeError) as error:
                            raise InputError(
                                f"依存元のWI本文を読み込めない: {name}: {error}", next_action=_FIX_PRIVATE_NOTES
                            ) from error
                        sources[name] = parsed[0] if parsed else {}
                        if source is not None and sources[name].get("state") is None:
                            sources[name]["state"] = source.parent.name
                source_name = evidence["依存元"]
                header = sources[source_name]

                if header.get("state") not in (WI_STATE_ADOPTED, WI_STATE_REJECTED):
                    actual = header.get("depends_on", [])
                    if actual != evidence["depends_on"]:
                        mismatches.append(f"depends_on: 根拠={evidence['depends_on']}、{source_name}のfrontmatter={actual}")
                for index, name in enumerate((first, second), 1):
                    repository = sources[name].get("target_repo")
                    resolved = _git_remote.resolve_repo_identifier(repository) if isinstance(repository, str) else None
                    if resolved != evidence[f"リポジトリ{index}"]:
                        mismatches.append(
                            f"リポジトリ{index}: 根拠={evidence[f'リポジトリ{index}']}、{name}のtarget_repo={resolved}"
                        )

            if mismatches:
                errors.append(
                    Violation(
                        f"初回配分: {first}と{second}の{kind}の結合根拠が元入力と一致しない: {'; '.join(mismatches)}",
                        next_action=_NEXT_SOURCE_MISMATCH,
                    )
                )
    return errors


def _candidate_records(path: pathlib.Path) -> list[dict[str, typing.Any]]:
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = [json.loads(line) for line in split_lines(text) if line.strip()]
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise ValueError("候補情報はJSON LinesまたはJSON objectの配列で指定する")
        return data
    except (OSError, UnicodeError, ValueError) as error:
        raise InputError(
            f"取得済み候補情報を読めない: {path}: {error}",
            next_action="--with-stalenessで取得済みのwi listの保存先を--candidate-fileへ指定する",
        ) from error


def _generate_selection(
    judgments: object, candidates: pathlib.Path | list[dict[str, typing.Any]], *, additional: bool
) -> dict[str, object]:
    @functools.cache
    def metadata(name: str) -> dict[str, typing.Any]:
        source = _plan_file.find_wi_source(name, _plan_file.private_notes_root())
        if source is None:
            raise InputError(f"生成に使うWI本文が無い: {name}", next_action=_FIX_PRIVATE_NOTES)
        parsed = _wi_frontmatter.parse_frontmatter(source.read_text(encoding="utf-8"))
        header = dict(parsed[0]) if parsed else {}
        repo = header.get("target_repo")
        if isinstance(repo, str):
            header["target_repo"] = _git_remote.resolve_repo_identifier(repo)
        return header

    try:
        return selection_contract.generate_selection(
            judgments,
            _candidate_records(candidates) if isinstance(candidates, pathlib.Path) else candidates,
            metadata,
            _resume_plan_id,
            additional=additional,
        )
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
        raise InputError(
            str(error),
            next_action=(
                "候補情報と意味判断を`agent-toolkit:atk-mode/references/process-wi-selection-format.md`"
                "「選定の生成入力」へそろえて再実行する"
            ),
        ) from error


def load_inline_addition(
    text: str, existing: dict[str, object], work_dir: pathlib.Path, candidate_file: pathlib.Path | None
) -> tuple[dict[str, object], object]:
    decisions = yaml.safe_load(text)
    if not isinstance(decisions, dict):
        raise ValueError("標準入力へ追加の意味判断YAMLを渡してください")
    rows = _selection.decisions(decisions)
    if not isinstance(rows, list) or not rows:
        raise ValueError("標準入力の`選定`へ追加するWIを指定してください")
    names = [row.get(_selection.WI_KEY) if isinstance(row, dict) else None for row in rows]
    if any(not isinstance(name, str) for name in names):
        raise ValueError("追加の判断にWIファイル名を指定してください")
    mapping = decisions.pop("レーン対応", None)
    if mapping is None:
        mapping = {
            row[_selection.LANE_KEY]: row[_selection.LANE_KEY]
            for row in rows
            if isinstance(row, dict) and isinstance(row.get(_selection.LANE_KEY), str) and row[_selection.LANE_KEY] != "なし"
        }
    if _selection.ESTIMATE_CORRECTION_KEY in existing:
        decisions.setdefault(_selection.ESTIMATE_CORRECTION_KEY, existing[_selection.ESTIMATE_CORRECTION_KEY])
    if candidate_file is None:
        environment = dict(os.environ)
        for name in AGENT_ENVIRONMENT_VARIABLES:
            environment.pop(name, None)
        launcher = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "launch.py"
        result = subprocess.run(
            [
                sys.executable,
                str(launcher),
                "agent_toolkit/atk.py",
                "wi",
                "list",
                "--jsonl",
                "--with-staleness",
                "--skip-pull",
                "--status=processable",
                "--target-branch=auto",
                "--target-repo",
                str(work_dir),
            ],
            env=environment,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode:
            raise ValueError(f"追加WIの候補を取得できない（終了コード{result.returncode}）: {result.stderr.strip()}")
        candidates = [json.loads(line) for line in split_lines(result.stdout) if line.strip()]
        excluded = decisions.get("除外候補", [])
        relevant = names + excluded if isinstance(excluded, list) else names
        candidates = [candidate for candidate in candidates if candidate.get("filename") in relevant]
    else:
        candidates = _candidate_records(candidate_file)
    return _generate_selection(decisions, candidates, additional=True), mapping


def load_selection(
    selection_file: pathlib.Path,
    *,
    additional: bool = False,
    candidate_file: pathlib.Path | None = None,
    reorder: bool = False,
) -> dict[str, object]:
    """選定結果を読み、YAML構文、欄名、必須の欄および値の型を確かめて返す。

    ファイルを開けない失敗はパスの誤りとして、読めた内容の誤りは選定結果の修正として、別の次の操作を付けて送出する。
    """
    try:
        text = selection_file.read_text(encoding="utf-8")
    except OSError as error:
        raise InputError(f"選定結果を読み込めない: {selection_file}: {error}", next_action=_FIX_PATHS) from error
    except UnicodeDecodeError as error:
        raise InputError(
            f"選定結果がUTF-8ではない: {selection_file}: {error}",
            next_action="選定結果をUTF-8で保存し直してから、同じコマンドを再実行する",
        ) from error
    try:
        selection = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise InputError(f"選定結果のYAML構文が不正: {selection_file}: {error}", next_action=_FIX_YAML) from error
    if candidate_file is not None:
        selection = _generate_selection(selection, candidate_file, additional=additional)
    elif not additional:
        _drop_write_scope_reasons(selection)
    if reorder and isinstance(selection, dict):
        costs = _selection.lane_costs(selection)
        if costs is not None:
            integrated = _selection.integrated_lanes(selection)
            for row in costs:
                if isinstance(row, dict) and row.get(_selection.LANE_KEY) not in integrated:
                    row.pop(_selection.STAGE_KEY, None)
                    row.pop(_selection.PRIOR_LANES_KEY, None)
            selection.pop("lane_costs", None)
            selection[_selection.LANE_COSTS_KEY] = costs
    errors, model_errors = selection_contract.structure_errors(selection)
    if candidate_file is not None and not additional and not (errors or model_errors) and derives_lane_order(selection):
        try:
            derive_lane_order(selection)
        except (KeyError, ValueError) as error:
            raise InputError(str(error), next_action=_FIX_CONTENT) from error
    if isinstance(selection, dict) and not additional and _selection.INITIAL_ALLOCATION_KEY not in selection:
        errors.append(
            "初回配分がない。初回候補WI・レーン割当・不可分成分の構成WIと結合条件を、"
            "元候補と初回選定の記録から補い、`agent-toolkit:atk-mode/references/process-wi-selection-format.md`"
            "「選定結果の書式」の`初回配分`の形式で再実行する"
        )
    if errors or model_errors:
        next_action = f"{_FIX_MODEL}。{_FIX_CONTENT}" if model_errors else _FIX_CONTENT
        raise InputError(
            "\n".join([f"選定結果の内容が不正: {selection_file}", *model_errors, *errors]), next_action=next_action
        )
    return typing.cast(dict[str, object], selection)


def _drop_write_scope_reasons(selection: object) -> None:
    if not isinstance(selection, dict):
        return
    items = selection.get(_selection.DECISIONS_KEY, selection.get("decisions"))
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            item.pop(_selection.WRITE_SCOPE_REASONS_KEY, None)
