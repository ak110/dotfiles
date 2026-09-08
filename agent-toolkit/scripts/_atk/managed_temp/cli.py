# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F821,I001
# pylint: disable=unused-import,used-before-assignment,wrong-import-order
#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""agent-toolkitが所有する一時ディレクトリを作成・検証・後始末する。"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import datetime
import enum
import hashlib
import json
import os
import pathlib
import re
import secrets
import shutil
import stat
import sys
import tempfile
import typing
import unicodedata
from ctypes import wintypes

from _atk import help_text as _atk_help
from _atk import output_file as _output_file


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _atk.managed_temp.windows_security import (
        _AccessAllowedAce,
        _AceHeader,
        _Acl,
        _AclSizeInformation,
        _ByHandleFileInformation,
        _FileTime,
        _WindowsAce,
        _WindowsSecurity,
        _validate_windows_managed_root_security,
        _validate_windows_security,
        _windows_acl_buffer,
        _windows_current_sid,
        _windows_current_user_ace_is_valid,
        _windows_dll,
        _windows_equal_sids,
        _windows_error,
        _windows_external_writer_ace_is_valid,
        _windows_handle_open_error,
        _windows_identity,
        _windows_information_identity,
        _windows_managed_root_security_is_valid,
        _windows_path_handle,
        _windows_replace_security,
        _windows_secure_path,
        _windows_security_base_is_valid,
        _windows_security_descriptor,
        _windows_security_from_handle,
        _windows_security_update_handle,
        _windows_set_security,
        _windows_sid_bytes,
    )
    from _atk.managed_temp.registry import (
        MAX_AGE_DAYS,
        ManagedTempError,
        _MARKER_NAME,
        _ManagedTempEntry,
        _PREFIX_RE,
        _PREFIX_RULES,
        _SCHEMA_VERSION,
        _UTC_ISO8601_RE,
        _ValidatedRoot,
        _ValidatedTemp,
        _WINDOWS_ACCESS_ALLOWED_ACE_TYPE,
        _WINDOWS_ACCESS_DENIED_ACE_TYPE,
        _WINDOWS_ACL_REVISION,
        _WINDOWS_CONTAINER_INHERIT_ACE,
        _WINDOWS_DACL_SECURITY_INFORMATION,
        _WINDOWS_ERROR_ACCESS_DENIED,
        _WINDOWS_EXTERNAL_WRITER_ACCESS,
        _WINDOWS_FILE_ALL_ACCESS,
        _WINDOWS_FILE_ATTRIBUTE_DIRECTORY,
        _WINDOWS_FILE_FLAG_BACKUP_SEMANTICS,
        _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
        _WINDOWS_FILE_SHARE_ALL,
        _WINDOWS_OBJECT_INHERIT_ACE,
        _WINDOWS_OPEN_EXISTING,
        _WINDOWS_OWNER_SECURITY_INFORMATION,
        _WINDOWS_PROTECTED_DACL_SECURITY_INFORMATION,
        _WINDOWS_READ_ATTRIBUTES,
        _WINDOWS_READ_CONTROL,
        _WINDOWS_REPARSE_POINT,
        _WINDOWS_SE_DACL_PROTECTED,
        _WINDOWS_SE_FILE_OBJECT,
        _WINDOWS_SYNCHRONIZE,
        _WINDOWS_WRITE_DAC,
        _WINDOWS_WRITE_OWNER,
        _WindowsApiError,
        _WindowsHandleOpenError,
        _awis_are_valid,
        _is_utc_iso8601,
        _load_marker,
        _load_private_json,
        _owner_record,
        _path_identity,
        _record,
        _record_base,
        _record_mismatch_error,
        _records_match,
        _registry_name,
        _registry_path,
        _state_root,
        _state_root_path,
        _temp_root,
        _validate_root,
        _write_marker,
        _write_private_json,
    )
    from _atk.managed_temp.creation import (
        _invalid_prefix_error,
        _remove_created_target,
        _validate_path_shape,
        _validate_posix,
        _validate_windows,
        create_managed_temp,
        is_valid_prefix,
        prefix_violation,
        validate_managed_temp,
    )
    from _atk.managed_temp.inventory import (
        _QuarantineJudgement,
        _QuarantineState,
        _classify_quarantine,
        _cleanup_missing_registered_temp,
        _cleanup_posix,
        _cleanup_quarantine,
        _cleanup_windows,
        _clear_directory,
        _consume_registry,
        _consuming_registry_path,
        _entity_absence_is_confirmed,
        _lstat_or_none,
        _marker_recovery_is_accepted,
        _report_unregistered_candidates,
        _restore_cleanup_marker,
        _restore_cleanup_state,
        _restore_interrupted_consume,
        _restore_posix_quarantine,
        _restore_registry,
        _tree_snapshot,
        _unregistered_candidates,
        cleanup_managed_temp,
        count_unregistered_candidates,
        is_missing_registered_temp,
        list_managed_temp,
        sweep_expired_managed_temp,
    )


from _atk.managed_temp.creation import *  # noqa: F403
from _atk.managed_temp.inventory import *  # noqa: F403
from _atk.managed_temp.registry import *  # noqa: F403


def build_parser(parser: argparse.ArgumentParser, *, command_dest: str = "command") -> None:
    """管理対象一時領域のサブコマンドを登録する。"""
    subparsers = _atk_help.add_subcommands(
        parser,
        dest=command_dest,
        required=False,
        show_help_when_missing=True,
    )
    create_parser = _atk_help.add_command(subparsers, "create", **_atk_help.HELP["atk managed-temp create"])
    create_parser.add_argument(
        "--prefix",
        required=True,
        help="作成するディレクトリ名の先頭へ付ける用途識別子。"
        + "。".join(description for description, _satisfied in _PREFIX_RULES)
        + "。",
    )
    create_parser.add_argument(
        "--root",
        type=pathlib.Path,
        help=("作成先の一時root。別のnamespaceからも同じ絶対パスで到達できる既存ディレクトリを指定する場合だけ使う。"),
    )
    create_parser.add_argument(
        "--awi",
        action="append",
        help="この領域が対応するAWIのファイル名。複数回指定できる",
    )
    cleanup_parser = _atk_help.add_command(subparsers, "cleanup", **_atk_help.HELP["atk managed-temp cleanup"])
    cleanup_parser.add_argument(
        "--path",
        type=pathlib.Path,
        help=(
            "後始末する管理対象一時ディレクトリの絶対パス。作成時に出力された値を指定する。"
            "省略した場合は現在の管理対象の絶対パスを示して終了する。"
        ),
    )
    cleanup_parser.add_argument(
        "--recover-registry",
        action="store_true",
        help="登録を失った管理対象を、実体側マーカーの検証を通過した場合に限り復元して後始末する",
    )
    list_parser = _atk_help.add_command(subparsers, "list", **_atk_help.HELP["atk managed-temp list"])
    list_parser.add_argument("--prefix", help="列挙する領域を用途識別子で限定する。")
    _output_file.add_output_file_arg(list_parser)


def dispatch(args: argparse.Namespace, *, command_dest: str = "command") -> int:
    """解析済み引数に対応する操作を実行し、終了状態を返す。"""
    try:
        if getattr(args, command_dest) == "create":
            print(
                create_managed_temp(
                    args.prefix,
                    getattr(args, "root", None),
                    tuple(getattr(args, "awi", None) or ()),
                )
            )
        elif getattr(args, command_dest) == "cleanup":
            if args.path is None:
                entries = list_managed_temp()
                if not entries:
                    raise ManagedTempError("--pathを指定してください。現在の管理対象はありません。")
                if len(entries) == 1:
                    raise ManagedTempError(
                        "--pathを指定してください。現在の管理対象は1件です。"
                        f"atk managed-temp cleanup --path {entries[0]['path']} を実行してください。"
                    )
                paths = "\n".join(entry["path"] for entry in entries)
                raise ManagedTempError(
                    f"--pathを指定してください。現在の管理対象の絶対パスを作成時刻の昇順で示します。\n{paths}"
                )
            cleanup_managed_temp(args.path, recover_registry=getattr(args, "recover_registry", False))
        else:
            entries = list_managed_temp(args.prefix, report_recovery_candidates=True)
            for entry in entries:
                print(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            return 0 if entries else 1
        return 0
    except ManagedTempError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    """CLI引数を解釈して管理対象一時ディレクトリを操作する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    build_parser(parser)
    return dispatch(parser.parse_args(argv))
