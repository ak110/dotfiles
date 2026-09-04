"""計画ファイルの保存root・参照表記・種別判定を扱う共通モジュール。

新規計画は`~/.claude/plans/`直下で作業し、実装レビュー完了後は作成日の年月階層を付けて
private-notesへ移す。計画本文が同じ計画に属する付属ファイルを参照する場合は、固定接頭辞
`~/.claude/plans/`とファイル名を用い、接頭辞を展開せず当該参照を含む計画ファイルの
ディレクトリを基準に解決する。これにより計画が作業rootと保存rootのどちらにあっても
同じ参照値が同じ実体を指す。計画の外にあるキューmetadataの`plan_file`は基準となる
計画ファイルを持たないため、移動後の位置を表す`$(atk config get private_notes)/`を
固定接頭辞として用いる。既存の日付階層の作業ファイル、計画本文に残る可搬表記及び
過去に保存された絶対パスは読み取り互換として受理する。
本モジュールはシェルを起動せず、参照値を通常の相対パスとして検証する。
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import subprocess

import platformdirs

PORTABLE_PLAN_PREFIX = "$(atk config get private_notes)/"
"""キューmetadataの`plan_file`と、計画本文に残る既存参照で受理する固定可搬接頭辞。"""

PLAN_ADJUNCT_REFERENCE_PREFIX = "~/.claude/plans/"
"""計画本文が同じ計画に属する付属ファイルを参照する固定接頭辞。"""

NEW_PLANS_DIRECTORY = "plans"
"""private-notes直下の新しい計画root名。"""

OWNER_RECORD_SUFFIX = ".owner.json"
"""計画バンドルの所有セッションを記録するファイルのサフィックス。"""

_OWNER_SESSION_ENVIRONMENT_KEYS = ("AGENT_TOOLKIT_OWNER_SESSION", "CLAUDE_CODE_SESSION_ID")

_FORBIDDEN_NAME_CHARACTERS = set('/\\:*?"<>|')
_CANONICAL_MAIN_RE = re.compile(r"(?P<day>[0-9]{2})-(?P<label>.+)-(?P<token>[0-9a-f]{4})\.md\Z")
_MIGRATED_MAIN_RE = re.compile(r"(?P<day>[0-9]{2})-(?P<legacy_name>.+\.md)\Z")


def private_notes_root(
    private_notes: pathlib.Path | str | None = None,
    *,
    home: pathlib.Path | str | None = None,
) -> pathlib.Path:
    """private-notesのrootを解決する。

    明示値、`AGENT_TOOLKIT_PRIVATE_NOTES`、既存の`~/private-notes`、
    `platformdirs`の順で採用する。root自体を自動作成しない。
    """
    if private_notes is not None:
        return pathlib.Path(private_notes).expanduser()
    override = os.environ.get("AGENT_TOOLKIT_PRIVATE_NOTES")
    if override:
        return pathlib.Path(override).expanduser()
    home_path = pathlib.Path(home).expanduser() if home is not None else pathlib.Path.home()
    default = home_path / "private-notes"
    if default.exists():
        return default
    return pathlib.Path(platformdirs.user_data_dir("agent-toolkit", appauthor=False)) / "private-notes"


def new_plans_root(private_notes: pathlib.Path | str | None = None) -> pathlib.Path:
    """保存済み計画rootの絶対パスを返す。"""
    return private_notes_root(private_notes) / NEW_PLANS_DIRECTORY


def working_plans_root(home: pathlib.Path | str | None = None) -> pathlib.Path:
    """実装レビュー完了まで使う計画作業rootの絶対パスを返す。"""
    home_path = pathlib.Path(home).expanduser() if home is not None else pathlib.Path.home()
    return home_path / ".claude" / "plans"


def legacy_plans_root(home: pathlib.Path | str | None = None) -> pathlib.Path:
    """旧直下形式も残る計画作業rootの絶対パスを返す。"""
    return working_plans_root(home)


def owner_record_path(main_plan_path: pathlib.Path | str) -> pathlib.Path:
    """計画ファイル（メイン）のパスから所有記録のパスを返す。"""
    path = pathlib.Path(main_plan_path)
    return path.with_name(path.stem + OWNER_RECORD_SUFFIX)


def resolve_owner_session_id() -> str | None:
    """所有記録へ書くセッション識別子を環境から解決する。

    委譲先には委譲元が`AGENT_TOOLKIT_OWNER_SESSION`で自身の識別子を渡す。
    当該値が無い場合は、実行中のセッション自身を示す`CLAUDE_CODE_SESSION_ID`を用いる。
    いずれも非空の値を持たない場合は解決しない。
    """
    for key in _OWNER_SESSION_ENVIRONMENT_KEYS:
        value = os.environ.get(key)
        if value:
            return value
    return None


def write_owner_record(main_plan_path: pathlib.Path | str, *, session_id: str) -> pathlib.Path:
    """計画バンドルの所有記録を出力し、出力したパスを返す。"""
    path = owner_record_path(main_plan_path)
    record = {"session_id": session_id, "recorded_at": datetime.datetime.now().astimezone().isoformat()}
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def record_plan_owner(main_plan_path: pathlib.Path | str) -> pathlib.Path | None:
    """所有セッションを解決できた場合だけ所有記録を出力し、出力したパスを返す。

    解決できない場合は記録を残さず、呼び出し元の取得・作成そのものは成功として扱う。
    """
    session_id = resolve_owner_session_id()
    if session_id is None:
        return None
    return write_owner_record(main_plan_path, session_id=session_id)


def read_owner_session_id(main_plan_path: pathlib.Path | str) -> str | None:
    """所有記録が示すセッション識別子を返す。

    記録が無い場合、JSONオブジェクトとして解釈できない場合、`session_id`が非空の文字列でない場合は
    いずれも所有を確定できないものとして`None`を返す。
    """
    try:
        record = json.loads(owner_record_path(main_plan_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    session_id = record.get("session_id")
    if isinstance(session_id, str) and session_id:
        return session_id
    return None


def remove_owner_record(main_plan_path: pathlib.Path | str) -> None:
    """計画バンドルの所有記録を回収する。"""
    owner_record_path(main_plan_path).unlink(missing_ok=True)


def _resolve(path: pathlib.Path) -> pathlib.Path:
    """存在しないパスも含めて実体基準の絶対パスへ変換する。"""
    return path.expanduser().resolve(strict=False)


def _lexically_under(path: pathlib.Path, root: pathlib.Path) -> bool:
    """symlinkを解決せずにpathがroot配下にあるかを判定する。"""
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError:
        return False
    return not any(part in ("", ".", "..") for part in relative.parts)


def _relative_to(path: pathlib.Path, root: pathlib.Path) -> pathlib.PurePath | None:
    """pathがroot配下ならroot相対値を返す。"""
    try:
        return _resolve(path).relative_to(_resolve(root))
    except (OSError, ValueError):
        return None


def _validate_portable_remainder(remainder: str) -> pathlib.PurePosixPath:
    """portable接頭辞後の相対値を検証する。"""
    if not remainder or "$(" in remainder or "\x00" in remainder:
        raise ValueError("計画ファイルの可搬パスが空、または別のシェル式を含んでいます")
    if "\\" in remainder:
        raise ValueError("計画ファイルの可搬パスにWindows区切り文字を指定できません")
    relative = pathlib.PurePosixPath(remainder)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise ValueError("計画ファイルの可搬パスはprivate-notes配下の相対パスで指定してください")
    return relative


def validate_plan_relative_path(relative_path: pathlib.Path | str) -> pathlib.Path:
    """新plans rootからの相対メイン計画パスを検証して返す。

    形式は`yyyy/MM/dd-{名称}-{小文字16進数4桁}.md`で、年・月・日の実在日付、
    パス文字、名称、16進数を検証する。
    """
    raw = os.fspath(relative_path)
    if not raw or "$(" in raw or "\x00" in raw or "\\" in raw:
        raise ValueError("計画ファイルの相対パスが不正です")
    relative = pathlib.PurePosixPath(raw)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise ValueError("計画ファイルの相対パスはplans root配下で指定してください")
    if len(relative.parts) != 3:
        raise ValueError("計画ファイルはyyyy/MM/ファイル名の形式で指定してください")
    year_text, month_text, filename = relative.parts
    if not (re.fullmatch(r"[0-9]{4}", year_text) and re.fullmatch(r"[0-9]{2}", month_text)):
        raise ValueError("計画ファイルの年月ディレクトリが不正です")
    match = _CANONICAL_MAIN_RE.fullmatch(filename)
    if match is None:
        raise ValueError("計画ファイル名はdd-{日本語の名称}-{小文字16進数4桁}.mdの形式で指定してください")
    label = match.group("label")
    if not label or any(character in _FORBIDDEN_NAME_CHARACTERS or ord(character) < 0x20 for character in label):
        raise ValueError("計画ファイル名に使用できない文字が含まれています")
    try:
        datetime.date(int(year_text), int(month_text), int(match.group("day")))
    except ValueError as error:
        raise ValueError("計画ファイル名の日付が不正です") from error
    return pathlib.Path(*relative.parts)


def validate_working_plan_relative_path(relative_path: pathlib.Path | str) -> pathlib.Path:
    """計画作業root直下のメイン計画パスを検証して返す。"""
    raw = os.fspath(relative_path)
    if not raw or "$(" in raw or "\x00" in raw or "\\" in raw:
        raise ValueError("作業中の計画ファイルの相対パスが不正です")
    relative = pathlib.PurePosixPath(raw)
    if relative.is_absolute() or len(relative.parts) != 1 or relative.parts[0] in ("", ".", ".."):
        raise ValueError("作業中の計画ファイルは計画作業root直下のファイル名で指定してください")
    filename = relative.name
    canonical = _CANONICAL_MAIN_RE.fullmatch(filename)
    migrated = _MIGRATED_MAIN_RE.fullmatch(filename)
    if canonical is not None:
        label = canonical.group("label")
        if not label or any(character in _FORBIDDEN_NAME_CHARACTERS or ord(character) < 0x20 for character in label):
            raise ValueError("作業中の計画ファイル名に使用できない文字が含まれています")
        day = int(canonical.group("day"))
    elif migrated is not None:
        legacy_name = migrated.group("legacy_name")
        if legacy_name.endswith((".detail.md", ".bugs.md", ".review.md", "-workaround-check.md")) or any(
            character in _FORBIDDEN_NAME_CHARACTERS or ord(character) < 0x20 for character in legacy_name
        ):
            raise ValueError("作業中の移行済み計画ファイル名に使用できない文字が含まれています")
        day = int(migrated.group("day"))
    else:
        raise ValueError("作業中の計画ファイル名はdd-{名称}-{小文字16進数4桁}.mdの形式で指定してください")
    if not 1 <= day <= 31:
        raise ValueError("作業中の計画ファイル名の日が不正です")
    return pathlib.Path(filename)


def _creation_epoch(path: pathlib.Path) -> float | None:
    """ファイルの作成日時を返し、取得できない環境ではNoneを返す。"""
    info = path.stat(follow_symlinks=False)
    value = getattr(info, "st_birthtime", None)
    if value is None:
        try:
            result = subprocess.run(
                ["stat", "--format=%W", "--", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return None
        if result.returncode != 0:
            return None
        try:
            value = float(result.stdout.strip())
        except ValueError:
            return None
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def _birth_epoch(path: pathlib.Path) -> float:
    """保存先の年月を決める日時を返す。

    作成日時を取得できない環境では更新日時へ後退する。
    保存先の年月は更新日時からも決まるため、取得不能を停止条件にしない。
    """
    creation = _creation_epoch(path)
    if creation is not None:
        return creation
    return float(path.stat(follow_symlinks=False).st_mtime)


def file_birth_date(path: pathlib.Path) -> datetime.date:
    """保存先の年月を決める日時を実行ホストのローカル日付へ変換する。"""
    return datetime.datetime.fromtimestamp(_birth_epoch(path)).date()


def validate_migrated_plan_relative_path(relative_path: pathlib.Path | str) -> pathlib.Path:
    """移行で生成した旧ファイル名の新root相対パスを検証して返す。

    移行先は旧ファイル名を維持するため、正規の新規作成形式にないパスも
    `dd-{旧ファイル名}.md`として存在する。この形式は新規作成には用いず、
    移行済み計画の判定とcommitだけで受理する。
    """
    raw = os.fspath(relative_path)
    if not raw or "$(" in raw or "\x00" in raw or "\\" in raw:
        raise ValueError("移行済み計画ファイルの相対パスが不正です")
    relative = pathlib.PurePosixPath(raw)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise ValueError("移行済み計画ファイルはplans root配下で指定してください")
    if len(relative.parts) != 3:
        raise ValueError("移行済み計画ファイルはyyyy/MM/dd-{旧ファイル名}の形式で指定してください")
    year_text, month_text, filename = relative.parts
    match = _MIGRATED_MAIN_RE.fullmatch(filename)
    if not (re.fullmatch(r"[0-9]{4}", year_text) and re.fullmatch(r"[0-9]{2}", month_text) and match):
        raise ValueError("移行済み計画ファイルの年月またはファイル名が不正です")
    legacy_name = match.group("legacy_name")
    if legacy_name.endswith((".detail.md", ".bugs.md", ".review.md", "-workaround-check.md")) or any(
        character in _FORBIDDEN_NAME_CHARACTERS or ord(character) < 0x20 for character in legacy_name
    ):
        raise ValueError("移行済み計画ファイル名に使用できない文字が含まれています")
    try:
        datetime.date(int(year_text), int(month_text), int(match.group("day")))
    except ValueError as error:
        raise ValueError("移行済み計画ファイルの日付が不正です") from error
    return pathlib.Path(*relative.parts)


def _main_candidate_for_new_path(path: pathlib.Path) -> pathlib.Path | None:
    """新root内のdetail/bugsから対応するメイン候補を返す。"""
    name = path.name
    if name.endswith((".review.md", "-workaround-check.md")):
        return None
    if name.endswith(".detail.md"):
        return path.with_name(name[: -len(".detail.md")] + ".md")
    if name.endswith(".bugs.md"):
        return path.with_name(name[: -len(".bugs.md")] + ".md")
    if name.endswith(".md"):
        return path
    return None


def _new_plan_kind(file_path: str | os.PathLike[str]) -> str | None:
    """作業root又は保存root内の計画ファイルの種別を返す。"""
    try:
        path = _resolve(pathlib.Path(file_path))
        working_root = _resolve(working_plans_root())
        saved_root = _resolve(new_plans_root())
        root = next(candidate for candidate in (working_root, saved_root) if path.is_relative_to(candidate))
        candidate = _main_candidate_for_new_path(path)
        if candidate is None:
            return None
        candidate_rel = candidate.relative_to(root)
        if root == working_root and len(candidate_rel.parts) == 1:
            validate_working_plan_relative_path(candidate_rel)
        else:
            try:
                validate_plan_relative_path(candidate_rel)
            except ValueError:
                validate_migrated_plan_relative_path(candidate_rel)
        if path.name.endswith(".bugs.md"):
            return "adjunct"
        if path.name.endswith(".detail.md"):
            return "detail"
        if path.name.endswith(".md"):
            return "main"
    except (OSError, StopIteration, ValueError):
        return None
    return None


def resolve_plan_file(
    value: pathlib.Path | str,
    *,
    private_notes: pathlib.Path | str | None = None,
    home: pathlib.Path | str | None = None,
    allow_legacy_absolute: bool = True,
    allow_working_fallback: bool = True,
) -> pathlib.Path:
    """保存済み計画参照を実ファイルパスへ解決する。

    portable値はprivate-notes内へ限定する。保存先が存在せず、同じファイル名の
    直下作業ファイル又は同じ日付相対パスの作業ファイルが存在する場合は作業実体を返す。過去の絶対パスは
    既存データを読むための互換経路として受理する。
    """
    raw = os.fspath(value)
    if not raw:
        raise ValueError("plan_fileが空です")
    notes = _resolve(private_notes_root(private_notes))
    working_root = _resolve(working_plans_root(home))
    if raw.startswith(PORTABLE_PLAN_PREFIX):
        relative = _validate_portable_remainder(raw[len(PORTABLE_PLAN_PREFIX) :])
        candidate = _resolve(notes / relative)
        if not candidate.is_relative_to(notes):
            raise ValueError("計画ファイルの可搬パスがprivate-notes外を指しています")
        if candidate.exists():
            return candidate
        if allow_working_fallback and relative.parts and relative.parts[0] == NEW_PLANS_DIRECTORY:
            if len(relative.parts) == 4:
                direct_candidate = _resolve(working_root / relative.name)
                direct_main = _main_candidate_for_new_path(direct_candidate)
                try:
                    if direct_main is None:
                        raise ValueError("計画ファイルの種別が不正です")
                    validate_working_plan_relative_path(direct_main.name)
                except ValueError:
                    pass
                else:
                    if direct_candidate.exists():
                        if not direct_candidate.is_relative_to(working_root):
                            raise ValueError("計画ファイルの作業パスが作業root外を指しています")
                        return direct_candidate
            working_candidate = _resolve(working_root.joinpath(*relative.parts[1:]))
            if working_candidate.exists():
                if not working_candidate.is_relative_to(working_root):
                    raise ValueError("計画ファイルの作業パスが作業root外を指しています")
                return working_candidate
        return candidate
    if "$(" in raw:
        raise ValueError("plan_fileには固定された可搬接頭辞以外のシェル式を指定できません")
    path = pathlib.Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("plan_fileは可搬表記または絶対パスで指定してください")
    resolved = _resolve(path)
    for root in (notes / NEW_PLANS_DIRECTORY, working_plans_root(home)):
        if _lexically_under(path, root) and not resolved.is_relative_to(_resolve(root)):
            raise ValueError("計画ファイルのシンボリックリンクが許可root外を指しています")
    if resolved.is_relative_to(notes) or resolved.is_relative_to(working_root):
        return resolved
    if allow_legacy_absolute:
        return resolved
    raise ValueError("plan_fileが許可された保存root外を指しています")


def require_saved_plan_file(
    value: pathlib.Path | str,
    *,
    private_notes: pathlib.Path | str | None = None,
    home: pathlib.Path | str | None = None,
) -> pathlib.Path:
    """記録するplan_file値を、その値が指す保存先の実体へ解決する。

    記録値の消費主体は計画作業rootへのフォールバックを持たないため、記録経路と着手可否判定は
    記録値をそのまま解決した実体だけを受理する。
    """
    path = resolve_plan_file(
        value,
        private_notes=private_notes,
        home=home,
        allow_working_fallback=False,
    )
    if not path.is_file():
        raise ValueError(
            "plan_fileの保存先に実体がありません。"
            "先に`atk plans commit <計画作業root直下のメイン計画ファイル名>`で計画バンドルを保存してください"
        )
    return path


def is_plan_adjunct_reference(value: pathlib.Path | str) -> bool:
    """計画本文の付属ファイル参照の固定接頭辞を持つ値かを返す。"""
    return os.fspath(value).startswith(PLAN_ADJUNCT_REFERENCE_PREFIX)


def validate_adjunct_reference_name(name: str) -> str:
    """付属ファイル参照の接頭辞に続くファイル名を検証して返す。"""
    if not name or "$(" in name or "\x00" in name:
        raise ValueError("計画本文の参照値が空、またはシェル式を含んでいます")
    if "\\" in name:
        raise ValueError("計画本文の参照値にWindows区切り文字を指定できません")
    if "/" in name or name in (".", ".."):
        raise ValueError("計画本文の参照値は計画ファイルと同じディレクトリのファイル名1件で指定してください")
    return name


def resolve_plan_adjunct_reference(value: pathlib.Path | str, *, plan_path: pathlib.Path | str) -> pathlib.Path:
    """計画本文の付属ファイル参照を実ファイルパスへ解決する。

    接頭辞は展開せず、当該参照を含む計画ファイルのディレクトリへファイル名を結合する。
    計画が作業rootと保存rootのどちらにあっても同じ参照値が同じ計画の実体を指す。
    """
    raw = os.fspath(value)
    if not is_plan_adjunct_reference(raw):
        raise ValueError(f"計画本文の参照値は`{PLAN_ADJUNCT_REFERENCE_PREFIX}`から始めてください")
    name = validate_adjunct_reference_name(raw[len(PLAN_ADJUNCT_REFERENCE_PREFIX) :])
    return _resolve(pathlib.Path(plan_path)).parent / name


def to_portable_plan_file(
    path: pathlib.Path | str,
    *,
    private_notes: pathlib.Path | str | None = None,
    home: pathlib.Path | str | None = None,
) -> str:
    """保存root又は作業root内の絶対パスをportable値へ変換する。

    旧直下形式または過去のroot外絶対パスは読み取り互換のため絶対表記を維持する。
    """
    notes = _resolve(private_notes_root(private_notes))
    resolved = resolve_plan_file(path, private_notes=notes, home=home)
    relative = _relative_to(resolved, notes)
    if relative is not None and relative.parts and relative.parts[0] == NEW_PLANS_DIRECTORY:
        return PORTABLE_PLAN_PREFIX + pathlib.PurePosixPath(*relative.parts).as_posix()
    working_relative = _relative_to(resolved, working_plans_root(home))
    if working_relative is not None:
        try:
            main_candidate = _main_candidate_for_new_path(resolved)
            if main_candidate is not None:
                relative_main = _relative_to(main_candidate, working_plans_root(home))
                if relative_main is None:
                    raise ValueError("計画メインファイルが作業root外です")
                if len(relative_main.parts) == 1:
                    validate_working_plan_relative_path(relative_main.as_posix())
                    birth_date = file_birth_date(main_candidate)
                    portable_parts = (
                        NEW_PLANS_DIRECTORY,
                        f"{birth_date.year:04d}",
                        f"{birth_date.month:02d}",
                        *working_relative.parts,
                    )
                else:
                    validate_plan_relative_path(relative_main.as_posix())
                    portable_parts = (NEW_PLANS_DIRECTORY, *working_relative.parts)
                return PORTABLE_PLAN_PREFIX + pathlib.PurePosixPath(*portable_parts).as_posix()
        except ValueError:
            pass
    return str(resolved)


def normalize_plan_file(
    value: pathlib.Path | str,
    *,
    private_notes: pathlib.Path | str | None = None,
) -> str:
    """保存用のplan_file値へ正規化する。"""
    return to_portable_plan_file(value, private_notes=private_notes)


def stored_plan_file_path(
    value: pathlib.Path | str,
    *,
    private_notes: pathlib.Path | str | None = None,
) -> pathlib.Path:
    """保存済みplan_fileを読み取り用実体へ解決する別名。"""
    return resolve_plan_file(value, private_notes=private_notes)


def _plan_file_name(file_path: str) -> str | None:
    """旧`~/.claude/plans/`直下のファイル名を返す。"""
    if not file_path:
        return None
    try:
        path = _resolve(pathlib.Path(file_path))
        plans_dir = _resolve(legacy_plans_root())
        relative = path.relative_to(plans_dir)
    except (OSError, ValueError):
        return None
    if len(relative.parts) != 1:
        return None
    return relative.parts[0]


def _is_component_name(name: str) -> bool:
    """旧rootの計画構成要素名を判定する。"""
    if (
        name.endswith(".review.md")
        or name.endswith(".codex.log")
        or name.endswith("-workaround-check.md")
        or name.endswith(".bugs.md")
    ):
        return False
    return name.endswith(".md")


def is_plan_component_file(file_path: str) -> bool:
    """計画ファイル（メイン）または計画ファイル（詳細）か判定する。"""
    name = _plan_file_name(file_path)
    if name is not None:
        return _is_component_name(name)
    try:
        path = resolve_plan_file(file_path)
    except (OSError, ValueError):
        return False
    return _new_plan_kind(path) in {"main", "detail"}


def is_plan_main_file(file_path: str) -> bool:
    """計画ファイル（メイン）か判定する。"""
    name = _plan_file_name(file_path)
    if name is not None:
        return _is_component_name(name) and not name.endswith(".detail.md")
    try:
        path = resolve_plan_file(file_path)
    except (OSError, ValueError):
        return False
    return _new_plan_kind(path) == "main"


def is_plan_adjunct_file(file_path: str) -> bool:
    """計画付属のbugsファイルか判定する。"""
    name = _plan_file_name(file_path)
    if name is not None:
        return name.endswith(".bugs.md")
    try:
        path = resolve_plan_file(file_path)
    except (OSError, ValueError):
        return False
    return _new_plan_kind(path) == "adjunct"


# 実装側で読みやすい公開別名。既存hookの関数名は維持する。
plan_root = new_plans_root
work_plan_root = working_plans_root
portable_path = to_portable_plan_file
resolve_stored_plan_file = resolve_plan_file
is_valid_plan_relative_path = validate_plan_relative_path
