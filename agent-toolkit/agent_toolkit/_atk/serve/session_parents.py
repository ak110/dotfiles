"""セッション一覧の親子付けと件数上限による切り詰めを、サーバーとリモートヘルパーで共有する。

サーバー側`sessions.py`とSSH先で単独実行する`atk_serve_sessions_remote_helper.py`の双方が読み込むため、
標準ライブラリと`agent_toolkit._common`の標準ライブラリだけを使うモジュールに限ってimportする。
親子付けの規則を2か所で別に持つと、一方だけに情報源を加えた場合にローカルとリモートで親子が一致しなくなる。

親子は、記録自身か記録を作成した主体が親を示す情報源だけから決め、時刻や作業ディレクトリから推測しない。
情報源は次の順に調べ、最初に親が決まったものを採用する。

1. Claude Codeのサブエージェントのmetadata（呼び出し元が`parent_path`として渡す）
2. 親の記録に残る`agents_server`の`start`系の結果（`delegated_ids`）
3. Codexの記録の最初の`session_meta`が示す親thread
4. `agents_server`のsession登録簿が持つ起動元

登録簿を最後に置くのは、最上位のClaude Codeが起動したMCPサーバーが起動時のsession識別子を保持し続け、
`/clear`の後は古い会話を指し得るためである。親と子の実行系（Claude CodeとCodex）は異なり得るため、
3を除き親の検索に実行系を使わない。
"""

from __future__ import annotations

import collections
import dataclasses
import typing


@dataclasses.dataclass(frozen=True)
class ParentSource:
    """親子付けに使う一覧の項目1件の値。"""

    path: str
    engine: str
    session_id: str
    subagent_parent_path: str | None
    """Claude Codeのサブエージェントのmetadataで決まった親の記録のパス。"""
    codex_parent_thread_id: str | None


def resolve_parent_paths(
    sources: typing.Sequence[ParentSource],
    *,
    delegated_ids: typing.Callable[[str, str], typing.Iterable[str]],
    launcher_of: typing.Callable[[str], str | None],
) -> dict[str, str]:
    """記録のパスから親の記録のパスへの対応を返す。親が決まらない記録は含めない。

    `delegated_ids`は記録のパスと実行系から、その記録が`start`系で起動した子sessionの識別子を返す。
    親の記録に残る起動結果は、Claude Codeのサブエージェント記録を除く全記録から読む。
    Codexのサブエージェントが`agents_server`で起動した委譲先も、その親から外さないためである。
    """
    by_id: dict[str, list[ParentSource]] = collections.defaultdict(list)
    for source in sources:
        by_id[source.session_id].append(source)

    def unique(session_id: str, child_path: str, engine: str | None = None) -> ParentSource | None:
        matches = [
            source
            for source in by_id.get(session_id, [])
            if source.path != child_path and (engine is None or source.engine == engine)
        ]
        return matches[0] if len(matches) == 1 else None

    parents = {source.path: source.subagent_parent_path for source in sources if source.subagent_parent_path}
    for parent in sources:
        if parent.subagent_parent_path is not None:
            continue
        for child_id in delegated_ids(parent.path, parent.engine):
            child = unique(child_id, parent.path)
            if child is not None:
                parents.setdefault(child.path, parent.path)
    for source in sources:
        if source.path in parents or source.codex_parent_thread_id is None:
            continue
        parent = unique(source.codex_parent_thread_id, source.path, "codex")
        if parent is not None:
            parents[source.path] = parent.path
    for source in sources:
        if source.path in parents:
            continue
        launcher = launcher_of(source.session_id)
        parent = unique(launcher, source.path) if launcher is not None else None
        if parent is not None:
            parents[source.path] = parent.path
    return parents


def limit_with_ancestors[T](
    ordered: typing.Sequence[T],
    limit: int,
    *,
    path_of: typing.Callable[[T], str | None],
    parent_of: typing.Callable[[T], str | None],
) -> list[T]:
    """並び順の先頭から`limit`件を残し、残した項目の祖先が外れていれば戻した一覧を返す。

    件数上限で古い親が外れると、その子が親を持たない項目として第1階層へ並ぶ。
    上限を超えるのは戻した祖先の件数に限り、戻した祖先は残した項目の後ろへ入力の順で並べる。
    """
    kept = list(ordered[:limit])
    by_path = {path: item for item in ordered if (path := path_of(item)) is not None}
    kept_paths = {path for item in kept if (path := path_of(item)) is not None}
    restored: set[str] = set()
    for item in kept:
        parent_path = parent_of(item)
        while parent_path is not None and parent_path not in kept_paths and parent_path not in restored:
            ancestor = by_path.get(parent_path)
            if ancestor is None:
                break
            restored.add(parent_path)
            parent_path = parent_of(ancestor)
    if not restored:
        return kept
    return kept + [item for item in ordered[limit:] if path_of(item) in restored]
