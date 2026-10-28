"""process-loopの担当の起動要否を、直前に進捗なしで終わった起動入力との差分で判定する。

単一起動とall-reposの両方が、担当を起動する前に本モジュールの`decide`を通す。
担当が進捗なしで終わった時点の起動入力をホストの状態ディレクトリへ対象リポジトリごとに記録し、
同じWI入力の間は起動を見送る。記録をWIの`cooldown_until`や`hold`で表すと、同じWIを処理できる
別ホストの起動まで止めるため、記録はこのホストだけが読む。時間経過による再試行は置かず、
入力の変化（WIの到着・本文や状態の変更・着手可否の区分の変化）、未判定アラート、および
`--resume`・`--auto-resume`の明示指定が起動を再開する。アラートの判定済み除外はreview-auditへ集約する。
"""

import dataclasses
import hashlib
import json
import pathlib

from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import listing as _listing
from agent_toolkit._atk.wi import process_loop_log as _log
from agent_toolkit._atk.wi import readiness as _readiness
from agent_toolkit._atk.wi.constants import WI_STATES
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._git import remote as _remote

RESTART_CHANGES = (
    "新しいWIの到着、既存WIの本文・状態・依存・冷却期限の変更、着手可否の区分の変化、"
    "ユーザーコメントやUWI回答の記入、新しい未判定のDependabotアラート、却下未完了の再試行、`--resume`の指定"
)

type Item = tuple[str, str, str]
"""起動入力のWI・診断の要素（ファイル名、frontmatterを含む本文のハッシュ、着手可否の区分）。"""


@dataclasses.dataclass(frozen=True)
class LaunchInput:
    """1回の起動判定に使う起動入力。`alerts`がNoneの反復はアラートを観測していない。"""

    items: frozenset[Item]
    alerts: frozenset[int] | None

    @property
    def names(self) -> frozenset[str]:
        """要素のファイル名の集合を返す。"""
        return frozenset(name for name, _digest, _kind in self.items)

    def with_alerts(self, alerts: frozenset[int] | None) -> LaunchInput:
        """アラート部分だけを差し替えた起動入力を返す。"""
        return dataclasses.replace(self, alerts=alerts)


@dataclasses.dataclass(frozen=True)
class Decision:
    """起動要否の判定結果。`skipped`は無進捗記録だけを理由に起動しないことを示す。"""

    launch: bool
    skipped: bool = False


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _items(
    entries: tuple[_wi_entries.EntryRecord, ...] | list[_wi_entries.EntryRecord],
    kinds: dict[str, str],
) -> frozenset[Item]:
    texts = {path.name: (text, state) for path, _repo, text, state, _kind in entries}
    return frozenset(
        (name, _digest(texts.get(name, ("", ""))[0]), f"{texts.get(name, ('', ''))[1]}:{kind}") for name, kind in kinds.items()
    )


def _repair_kinds(readiness: _readiness.ReadinessResult) -> dict[str, str]:
    kinds: dict[str, str] = {}
    for kind, names in (
        ("frontmatter-broken", readiness.frontmatter_broken_needs_uwi),
        ("invalid-dependency", readiness.invalid_dependencies),
        ("missing-dependency", readiness.missing_dependencies),
        ("self-dependency", readiness.self_dependencies),
        ("cyclic-dependency", readiness.cyclic_dependencies),
        ("invalid-cooldown", readiness.invalid_cooldowns),
    ):
        for name in names:
            kinds.setdefault(name, kind)
    return kinds


def repository_input(snapshot: _listing.RepositorySnapshot, alerts: frozenset[int] | None) -> LaunchInput:
    """all-reposの起動条件（候補と対処を要する診断）と同じ集合から起動入力を作成する。"""
    readiness = snapshot.readiness
    kinds = _repair_kinds(readiness)
    for path, _repo, _text, state, _kind in snapshot.candidates:
        name = path.name
        kinds[name] = (
            "processing" if state == "processing" else "ready" if name in readiness.ready else "internal-dependency-wait"
        )
    return LaunchInput(_items(snapshot.entries, kinds), alerts)


def target_input(private_notes: pathlib.Path, target_repo: str, alerts: frozenset[int] | None) -> LaunchInput:
    """単一起動の起動条件（着手可能な項目と対処を要する診断）と同じ集合から起動入力を作成する。"""
    readiness = _readiness.calculate_readiness(private_notes, target_repo)
    kinds = _repair_kinds(readiness)
    for name in readiness.ready:
        kinds[name] = "ready"
    entries = list(_wi_entries.iter_entries(private_notes, WI_STATES, target_repo))
    return LaunchInput(_items(entries, kinds), alerts)


def record_directory() -> pathlib.Path:
    """無進捗記録の保存先を返す。対象リポジトリの数だけファイルを持つ。"""
    return _state_paths.state_dir() / "process-loop-no-progress"


def record_failure_next_action() -> str:
    """無進捗記録を保存できなかった場合の次の操作を返す。

    保存できない間は見送りが働かず、進捗なしで終わった担当も同じ起動入力のまま次の反復で起動する。
    """
    return (
        f"{record_directory()}の書込権限と空き容量を確かめる"
        "（解消するまでは、進捗なしで終わった担当も同じ起動入力のまま次の反復で起動する）"
    )


def _path(repo_id: str) -> pathlib.Path:
    digest = hashlib.sha256(_remote.normalize_remote_url(repo_id).encode("utf-8")).hexdigest()
    return record_directory() / f"{digest}.json"


def _load(repo_id: str) -> LaunchInput | None:
    path = _path(repo_id)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        items = frozenset((str(name), str(digest), str(kind)) for name, digest, kind in record["items"])
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ActionableError(
            f"process-loopの無進捗記録を解釈できない: {path}: {error}",
            next_action=f"{path}を削除してからprocess-loopを再起動する（次の反復は記録なしで担当を起動する）",
        ) from error
    return LaunchInput(items, None)


def clear(repo_id: str) -> None:
    """対象リポジトリの無進捗記録を削除する。

    見送らずに担当を起動した時点と、候補・診断が無くアラートの取得に成功して0件だった時点で呼ぶ。
    """
    _path(repo_id).unlink(missing_ok=True)


def decide(repo_id: str, current: LaunchInput, *, explicit: bool = False) -> Decision:
    """担当を起動する仕事（候補・診断・未判定アラート）がある反復で、起動するか見送るかを返す。

    無進捗記録が無いか、記録に無いWI要素または未判定アラートが起動入力にあれば起動する。
    明示指定（`--resume`・`--auto-resume`の対象）は記録によらず起動する。アラート未観測時はWI部分を比べる。
    起動入力が空の反復の記録の削除は呼び出し側が`clear`で行う。
    """
    if explicit:
        return Decision(launch=True)
    record = _load(repo_id)
    if record is None:
        return Decision(launch=True)
    # 判定済み除外はreview-auditが確認版に限定して行う。未markの却下失敗をここで再び抑止しない。
    if current.items - record.items or current.alerts:
        return Decision(launch=True)
    return Decision(launch=False, skipped=True)


def record_no_progress(repo_id: str, start: LaunchInput, end: LaunchInput) -> None:
    """担当の起動時と終了時の両方に存在した要素だけを無進捗記録へ保存する。

    稼働中に加わった要素と変わった要素は記録に含まれず、次の反復で起動の対象になる。
    アラートの除外は確認版付きの判定記録が担うため、番号をこの記録へ複製しない。
    """
    items = start.items & end.items
    if not items:
        return
    path = _path(repo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "repo_id": repo_id,
        "items": sorted(list(item) for item in items),
    }
    atomic_write(path, json.dumps(body, ensure_ascii=False) + "\n")


def unresolved(start: LaunchInput, end: LaunchInput) -> bool:
    """起動時の要素が1件も解消していないかを返す。アラートの解消を確定できなければ偽を返す。

    解消はWIが処理可能な集合から外れること（ファイル名が終了時の起動入力から無くなること）と、
    未判定アラートが未判定から外れることを指す。
    """
    if not start.items and not start.alerts:
        return False
    if not start.names <= end.names:
        return False
    if start.alerts:
        return end.alerts is not None and start.alerts <= end.alerts
    return True


def report_skip(repo_id: str) -> None:
    """見送った反復の対象・理由・起動を再開する変化を端末とprocess-loopログへ出力する。"""
    reason = "前回の担当が進捗なしで終わり、起動入力が変わっていない"
    _log.append("launch_skipped_no_progress", target_repo=repo_id, reason=reason)
    print(f"{repo_id}: {reason}ため担当の起動を見送ります。起動を再開する変化: {RESTART_CHANGES}。", flush=True)
