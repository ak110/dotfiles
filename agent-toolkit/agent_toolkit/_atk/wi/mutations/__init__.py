# ruff: noqa: E402,E501,F401,F811,I001
# pylint: disable=invalid-name,wrong-import-order,wrong-import-position
# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,I001
# pylint: disable=invalid-name,wrong-import-order,wrong-import-position
# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
"""分割前の公開契約を維持する責務別実装パッケージ。"""

import sys
import types

from agent_toolkit._atk.wi.mutations import content as _content
from agent_toolkit._atk.wi.mutations import dependencies as _dependencies
from agent_toolkit._atk.wi.mutations import targets as _targets
from agent_toolkit._atk.wi.mutations import transitions as _transitions

_MODULES = (
    _targets,
    _transitions,
    _content,
    _dependencies,
)

for _target in _MODULES:
    for _source in _MODULES:
        for _name, _value in vars(_source).items():
            if not _name.startswith("__"):
                vars(_target).setdefault(_name, _value)

for _source in _MODULES:
    for _name, _value in vars(_source).items():
        if not _name.startswith("__"):
            globals().setdefault(_name, _value)

from agent_toolkit._atk.wi.mutations.content import (
    _build_noninteractive_edit_content,
    _cmd_append,
    _cmd_edit,
    _preserve_agent_user_comment,
    _reject_agent_user_comment_change,
    _reject_agent_user_comment_message,
    append_entry_content,
    edit_entry_content,
)
from agent_toolkit._atk.wi.mutations.dependencies import (
    _active_dependency_graph,
    _cmd_set_dependencies,
    _dependency_reaches,
    _entry_dependencies,
    set_entry_dependencies,
)
from agent_toolkit._atk.wi.mutations.targets import (
    _GIT_TIMEOUT_SECONDS,
    TRANSITION_EXPLICIT_STATES,
    TYPE_CHECKING,
    WI_EDITABLE_STATES,
    WI_PROCESSABLE_STATES,
    WI_STATE_ADOPTED,
    WI_STATE_HOLD,
    WI_STATE_INBOX,
    WI_STATE_PROCESSING,
    WI_STATE_REJECTED,
    WI_STATES,
    WI_TYPE_AWI,
    WI_TYPE_UWI,
    WebInputError,
    _add,
    _append_entry,
    _atk_git_sync,
    _atomic_write_text,
    _candidate_local_worktree,
    _cmd_commit,
    _commit_and_push,
    _commit_values_by_path,
    _copy_to_tempfile,
    _dedup_positional_filenames,
    _edit_entry,
    _entry_target_repo,
    _frontmatter,
    _git_head,
    _invalidate_repo_bound_metadata,
    _local_worktree_repo_id,
    _normalize_remote_url,
    _plan_file,
    _plan_format,
    _pull,
    _push_pending_commits,
    _bulk,
    _repo_lock,
    _require_type,
    _resolve_awi_targets,
    _resolve_commit,
    _resolve_commit_oid,
    _resolve_editable_targets,
    _resolve_processable_targets,
    _resolve_active_targets,
    _resolve_repo_id,
    _stamp_result,
    _subdir,
    _user_comment,
    _uwi,
    _validate_filename,
    _validate_filenames_only,
    _verify_target_repo_content,
    argparse,
    commit_entries,
    datetime,
    is_agent_environment,
    normalized_wi_type,
    os,
    pathlib,
    re,
    shutil,
    subprocess,
    sys,
    tempfile,
    typing,
)
from agent_toolkit._atk.wi.mutations.transitions import (
    _apply_transition,
    _cmd_adopt,
    _cmd_hold,
    _cmd_reject,
    _cmd_return_to_inbox,
    _cmd_rm,
    _cmd_start_processing,
    _cmd_unhold,
    _resolve_transition_paths,
    _strip_result_section,
    _transition_commit_message,
    _update_transition_metadata,
    _validate_transition_options,
    _validate_transition_targets,
    transition_entries,
)


class _PackageModule(types.ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for module in _MODULES:
            if name in vars(module):
                setattr(module, name, value)


sys.modules[__name__].__class__ = _PackageModule

__all__ = [
    "TRANSITION_EXPLICIT_STATES",
    "TYPE_CHECKING",
    "WI_EDITABLE_STATES",
    "WI_PROCESSABLE_STATES",
    "WI_STATES",
    "WI_STATE_ADOPTED",
    "WI_STATE_HOLD",
    "WI_STATE_INBOX",
    "WI_STATE_PROCESSING",
    "WI_STATE_REJECTED",
    "WI_TYPE_AWI",
    "WI_TYPE_UWI",
    "WebInputError",
    "_GIT_TIMEOUT_SECONDS",
    "_active_dependency_graph",
    "_add",
    "_append_entry",
    "_apply_transition",
    "_atk_git_sync",
    "_atomic_write_text",
    "_build_noninteractive_edit_content",
    "_candidate_local_worktree",
    "_cmd_adopt",
    "_cmd_append",
    "_cmd_commit",
    "_cmd_edit",
    "_cmd_hold",
    "_cmd_reject",
    "_cmd_return_to_inbox",
    "_cmd_rm",
    "_cmd_set_dependencies",
    "_cmd_start_processing",
    "_cmd_unhold",
    "_commit_and_push",
    "_commit_values_by_path",
    "_copy_to_tempfile",
    "_dedup_positional_filenames",
    "_dependency_reaches",
    "_edit_entry",
    "_entry_dependencies",
    "_entry_target_repo",
    "_frontmatter",
    "_git_head",
    "_invalidate_repo_bound_metadata",
    "_local_worktree_repo_id",
    "_normalize_remote_url",
    "_plan_file",
    "_plan_format",
    "_preserve_agent_user_comment",
    "_pull",
    "_push_pending_commits",
    "_reject_agent_user_comment_change",
    "_reject_agent_user_comment_message",
    "_bulk",
    "_repo_lock",
    "_require_type",
    "_resolve_awi_targets",
    "_resolve_editable_targets",
    "_resolve_commit",
    "_resolve_commit_oid",
    "_resolve_processable_targets",
    "_resolve_active_targets",
    "_resolve_repo_id",
    "_resolve_transition_paths",
    "_stamp_result",
    "_strip_result_section",
    "_subdir",
    "_transition_commit_message",
    "_update_transition_metadata",
    "_user_comment",
    "_uwi",
    "_validate_filename",
    "_validate_filenames_only",
    "_validate_transition_options",
    "_validate_transition_targets",
    "_verify_target_repo_content",
    "append_entry_content",
    "argparse",
    "commit_entries",
    "datetime",
    "edit_entry_content",
    "is_agent_environment",
    "normalized_wi_type",
    "os",
    "pathlib",
    "re",
    "set_entry_dependencies",
    "shutil",
    "subprocess",
    "sys",
    "tempfile",
    "transition_entries",
    "typing",
]
