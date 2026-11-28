from __future__ import annotations

import dataclasses
import hashlib
import pathlib
import re
import subprocess
from graphlib import CycleError, TopologicalSorter

from agent_toolkit._atk import outcome
from agent_toolkit._atk.wi import filenames, frontmatter, layout
from agent_toolkit._atk.wi.constants import WI_TYPE_AWI, WI_TYPE_UWI
from agent_toolkit._atk.wi.repo import resolve_repo_id_or_raise
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._git import command as _git_command

KEY = "key"
NEEDS = "needs"
PROPOSAL_ID_KEY = "proposal_id"
FINGERPRINT = "proposal_fingerprint"
RETRY_FINGERPRINT = "draft_retry_fingerprint"
_KEY = re.compile(r"[A-Za-z0-9_-]+")
_REFERENCE = re.compile(r"\{\{key:([^{}]*)\}\}")
_NEXT = "原稿のkey・needs・本文の参照を見直してから、原稿の組を再投入する"


@dataclasses.dataclass(frozen=True)
class Draft:
    position: int
    metadata: dict[str, object]
    body: str
    key: str | None
    needs: tuple[str, ...]
    references: tuple[str, ...]
    fingerprint: str | None

    @property
    def label(self) -> str:
        return f"{self.position + 1}件目の原稿" + (f"（key: {self.key}）" if self.key else "")


@dataclasses.dataclass(frozen=True)
class Insertion:
    position: int
    filename: str
    message: tuple[dict[str, object], str]
    depends_on: tuple[str, ...]


def _error(position: int, reason: str) -> WebInputError:
    return WebInputError(f"{position + 1}件目の原稿: {reason}", next_action=_NEXT)


def _draft(position: int, data: dict[str, object], body: str, namespace: str | None, entry_type: str) -> Draft:
    key = data.get(KEY)
    if KEY in data and (not isinstance(key, str) or _KEY.fullmatch(key) is None):
        raise _error(position, "keyは英数字・ハイフン・アンダースコアで指定する")
    needs = data.get(NEEDS, [])
    if not isinstance(needs, list) or any(not isinstance(value, str) or not value for value in needs):
        raise _error(position, "needsはキーまたは保存済みWIのファイル名の一覧で指定する")
    references = tuple(dict.fromkeys(_REFERENCE.findall(body)))
    if entry_type == WI_TYPE_UWI and (needs or references):
        raise _error(position, "UWIはneedsまたは本文のキー参照で依存先を指定できない")
    proposal = data.get(PROPOSAL_ID_KEY)
    if PROPOSAL_ID_KEY in data and (not isinstance(proposal, str) or not proposal.strip()):
        raise _error(position, "proposal_idは空でない文字列で指定する")
    if proposal is None and namespace and isinstance(key, str):
        proposal = f"{namespace}/{key}"
    metadata = {name: value for name, value in data.items() if name not in (KEY, NEEDS)}
    digest = None
    if isinstance(proposal, str):
        declared = dict(sorted(data.items()))
        digest = hashlib.sha256(frontmatter.serialize_frontmatter(declared, body).encode()).hexdigest()
        metadata.update({PROPOSAL_ID_KEY: proposal, FINGERPRINT: digest})
    return Draft(position, metadata, body, key if isinstance(key, str) else None, tuple(needs), references, digest)


def _registered(private_notes: pathlib.Path, proposals: set[str], *, reject_duplicates: bool) -> dict[str, tuple[str, object]]:
    result: dict[str, tuple[str, object]] = {}
    if proposals:
        for path in layout.iter_entry_paths(private_notes):
            parsed = frontmatter.parse_frontmatter(frontmatter.decode_entry_text(path.read_bytes()))
            if parsed is not None and isinstance(value := parsed[0].get(PROPOSAL_ID_KEY), str) and value in proposals:
                if reject_duplicates and value in result:
                    raise WebInputError(
                        f"提案ID {value} が複数の保存済みWIにある: {result[value][0]}、{path.name}",
                        next_action="登録済みWIを確かめ、別のproposal_idを付ける",
                    )
                result.setdefault(value, (path.name, parsed[0].get(FINGERPRINT)))
    return result


def retry_fingerprint(namespace: str, data: dict[str, object], body: str) -> str:
    # target_commitは起草の基点であり、更新後に同じ原稿を再投入しても別の要求にはならない。 pyfltr: allow-comment
    stable = {key: value for key, value in sorted(data.items()) if key != "target_commit"}
    content = frontmatter.normalize_newlines(frontmatter.serialize_frontmatter(stable, body))
    return hashlib.sha256(f"{namespace}\0{content}".encode()).hexdigest()


def registered_retries(private_notes: pathlib.Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in layout.iter_entry_paths(private_notes):
        parsed = frontmatter.parse_frontmatter(frontmatter.decode_entry_text(path.read_bytes()))
        if parsed is not None and parsed[0].get("type") == WI_TYPE_AWI:
            fingerprint = parsed[0].get(RETRY_FINGERPRINT)
            if isinstance(fingerprint, str):
                result.setdefault(fingerprint, path.name)
    return result


def legacy_proposal_positions(private_notes: pathlib.Path, messages: list[tuple[dict[str, object], str]]) -> list[int]:
    proposals = [data.get(PROPOSAL_ID_KEY) for data, _ in messages]
    registered = _registered(private_notes, {value for value in proposals if isinstance(value, str)}, reject_duplicates=False)
    seen: set[str] = set()
    kept: list[int] = []
    for index, proposal in enumerate(proposals):
        if isinstance(proposal, str):
            if proposal in registered:
                name, _ = registered[proposal]
                outcome.report_warning(
                    f"提案ID {proposal} のAWIは登録済みのため登録しなかった: {name}",
                    next_action=f"`atk wi show {name}`で登録済みの内容を確かめる",
                    source="wi-draft-already-registered",
                )
                continue
            if proposal in seen:
                outcome.report_warning(
                    f"提案ID {proposal} の原稿が同じ追加操作に複数あるため、最初の原稿だけを登録した",
                    next_action="原稿の提案IDを見直し、別の提案なら別のIDを付けて追加する",
                    source="wi-draft-duplicate-proposal",
                )
                continue
            seen.add(proposal)
        kept.append(index)
    return kept


def prepare(
    private_notes: pathlib.Path,
    messages: list[tuple[dict[str, object], str]],
    *,
    entry_type: str,
    namespace: str | None,
    timestamp: str | None,
    depends_on: tuple[str, ...],
) -> tuple[list[Insertion], dict[str, str]]:
    drafts = [_draft(index, data, body, namespace, entry_type) for index, (data, body) in enumerate(messages)]
    keys: dict[str, Draft] = {}
    for draft in drafts:
        if draft.key is not None:
            if draft.key in keys:
                raise _error(draft.position, f"keyが{keys[draft.key].label}と重複する: {draft.key}")
            keys[draft.key] = draft
    exists = filenames.existing_entry_filenames(private_notes) if any(draft.needs for draft in drafts) else set()
    graph: TopologicalSorter[int] = TopologicalSorter()
    referenced: set[str] = set()
    for draft in drafts:
        graph.add(draft.position)
        for value in draft.needs:
            if value not in keys and value not in exists:
                raise _error(draft.position, f"needsに指定したキーまたはWIが無い: {value}")
        for value in draft.references:
            if value not in keys:
                raise _error(draft.position, f"本文に指定したキーが無い: {value}")
        for key in (*draft.needs, *draft.references):
            if key in keys:
                referenced.add(key)
        graph.add(draft.position, *(keys[key].position for key in draft.needs if key in keys))
    try:
        graph.prepare()
    except CycleError as error:
        cycle = " → ".join(drafts[position].label for position in error.args[1])
        raise WebInputError(f"原稿の参照が循環している: {cycle}", next_action=_NEXT) from error
    proposals = {proposal for draft in drafts if isinstance(proposal := draft.metadata.get(PROPOSAL_ID_KEY), str)}
    registered = _registered(private_notes, proposals, reject_duplicates=True)
    names: dict[int, str] = {}
    remaining: list[Draft] = []
    seen: dict[str, Draft] = {}
    for draft in drafts:
        proposal = draft.metadata.get(PROPOSAL_ID_KEY)
        if isinstance(proposal, str):
            if (earlier := seen.get(proposal)) is not None:
                if earlier.fingerprint != draft.fingerprint:
                    raise _error(draft.position, f"同じ投入の中で内容が異なるproposal_idが重複する: {proposal}")
                continue
            seen[proposal] = draft
            if proposal in registered:
                name, fingerprint = registered[proposal]
                _check_registered(draft, name, fingerprint, referenced)
                names[draft.position] = name
                continue
        remaining.append(draft)
    if timestamp is None:
        names.update((draft.position, "保存時に決まる") for draft in remaining)
    else:
        counter = filenames.max_existing_seq(private_notes, timestamp) + 1
        for draft in remaining:
            names[draft.position] = f"{timestamp}-{counter:03d}.md"
            counter += 1
    resolved = {key: names[draft.position] for key, draft in keys.items()}
    return [
        Insertion(
            draft.position,
            names[draft.position],
            (draft.metadata, _REFERENCE.sub(lambda match: resolved[match[1]], draft.body)),
            tuple(dict.fromkeys((*depends_on, *(resolved.get(value, value) for value in draft.needs)))),
        )
        for draft in remaining
    ], resolved


def _check_registered(draft: Draft, name: str, fingerprint: object, referenced: set[str]) -> None:
    if fingerprint is None:
        if draft.key in referenced:
            raise WebInputError(
                f"{draft.label}の登録済みWIに原稿の指紋が無いため、同一と確認できない: {name}",
                next_action=f"依存先を{draft.key}ではなく{name}とする。本文の参照にも正式名を直接書いて再投入する",
            )
        outcome.report_warning(
            f"原稿の指紋が無い登録済みWIを再登録しなかった: {name}",
            next_action=f"`atk wi show {name}`で登録済みの内容を確かめる",
            source="wi-draft-missing-fingerprint",
        )
    elif fingerprint != draft.fingerprint:
        raise WebInputError(
            f"{draft.label}の内容が同じ提案IDで登録済みの原稿と異なる: {name}",
            next_action=f"別のproposal_idを指定するか、`atk wi edit {name} --body-file <原稿>`で更新する",
        )
    else:
        warn_already_registered(name)


def warn_already_registered(name: str) -> None:
    outcome.report_warning(
        f"同じ原稿は登録済みのため再登録しなかった: {name}",
        next_action=f"`atk wi show {name}`で登録済みの内容を確かめる",
        source="wi-draft-already-registered",
    )


DRAFT_BASIS_NEXT = "上流があれば取得し、無ければ宛先ブランチを使って基点を選び直す。影響する前提を調べ、原稿を検収し直す"


@dataclasses.dataclass(frozen=True)
class DraftBasis:
    repo: str
    worktree: pathlib.Path
    branch_ref: str
    oid: str

    def verify(self, registered_branch: str | None) -> None:
        def git(*args: str) -> subprocess.CompletedProcess[str]:
            return _git_command.run(["-C", str(self.worktree), *args], capture_output=True, text=True, check=False, timeout=10)

        branch_ref = self.branch_ref
        if not branch_ref.startswith("refs/heads/"):
            if "/" in branch_ref:
                candidates: set[str] = set()
                local_ref = f"refs/heads/{branch_ref}"
                if git("show-ref", "--verify", "--quiet", local_ref).returncode == 0:
                    candidates.add(local_ref)
                current_branch = git("symbolic-ref", "--quiet", "HEAD")
                destination = (
                    f"refs/heads/{registered_branch}" if registered_branch is not None else current_branch.stdout.strip()
                )
                if destination.startswith("refs/heads/") and branch_ref.partition("/")[2] == destination.removeprefix(
                    "refs/heads/"
                ):
                    candidates.add(destination)
                candidate = next(iter(candidates)) if len(candidates) == 1 else None
                raise self._invalid_branch(candidate)
            branch_ref = f"refs/heads/{branch_ref}"
            if git("show-ref", "--verify", "--quiet", branch_ref).returncode != 0:
                raise self._invalid_branch()
        if git("check-ref-format", branch_ref).returncode != 0:
            raise self._invalid_branch()
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", self.oid) is None:
            raise WebInputError("起草の基点には完全なOIDが必要", next_action=DRAFT_BASIS_NEXT)
        branch = branch_ref.removeprefix("refs/heads/")
        if registered_branch is not None:
            if registered_branch != branch:
                raise WebInputError("起草のベースが登録済みの宛先系統と一致しない", next_action=DRAFT_BASIS_NEXT)
            ref = f"refs/remotes/origin/{branch}"
        else:
            current_branch = git("symbolic-ref", "--quiet", "HEAD")
            if current_branch.returncode != 0 or current_branch.stdout.strip() != branch_ref:
                raise WebInputError("起草のベースが宛先worktreeの現在のブランチと一致しない", next_action=DRAFT_BASIS_NEXT)
            result = git("for-each-ref", "--format=%(refname)%09%(upstream)", branch_ref)
            if result.returncode != 0:
                raise WebInputError("起草のベースrefを照会できない", next_action=DRAFT_BASIS_NEXT)
            selected = next(
                (line.partition("\t")[2] for line in result.stdout.splitlines() if line.partition("\t")[0] == branch_ref),
                None,
            )
            if selected is None:
                raise WebInputError("起草のベースrefが宛先のリポジトリに無い", next_action=DRAFT_BASIS_NEXT)
            if not selected:
                configured = (git("config", "--get", f"branch.{branch}.{key}") for key in ("remote", "merge"))
                if any(setting.returncode != 1 for setting in configured):
                    raise WebInputError("設定済みの宛先上流を解決できない", next_action=DRAFT_BASIS_NEXT)
            ref = selected or branch_ref
        current = git("rev-parse", "--verify", f"{ref}^{{commit}}")
        if current.returncode != 0:
            raise WebInputError(f"取得済みの宛先refを解決できない: {ref}", next_action=DRAFT_BASIS_NEXT)
        if current.stdout.strip() != self.oid:
            raise WebInputError(f"起草の基点が取得済みの宛先refと一致しない: {ref}", next_action=DRAFT_BASIS_NEXT)

    @staticmethod
    def _invalid_branch(candidate: str | None = None) -> WebInputError:
        forms = "refs/heads/<ブランチ>、または宛先に実在する / を含まないブランチ名"
        action = (
            f"--draft-baseのブランチには {candidate} を指定する"
            if candidate
            else f"--draft-baseのブランチを {forms} で指定する"
        )
        return WebInputError(f"起草のベースには {forms} が必要", next_action=action)


def ensure_destinations(
    basis: DraftBasis,
    messages: list[tuple[dict[str, object], str]],
    branches: list[str | None],
    commit_branch: str | None,
) -> None:
    for (metadata, _), branch in zip(messages, branches, strict=True):
        raw = metadata.get("target_repo", basis.repo)
        if not isinstance(raw, str) or resolve_repo_id_or_raise(raw) != basis.repo or branch != commit_branch:
            raise WebInputError("起草の基点を使う原稿の宛先が別repoまたは別系統", next_action=DRAFT_BASIS_NEXT)
