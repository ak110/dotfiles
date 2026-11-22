import pathlib

from agent_toolkit._atk.wi import filenames as _wi_filenames
from agent_toolkit._common import next_action as _next_action


class WebInputError(_next_action.ActionableError):
    pass


def web_input_error_from(error: Exception, *, next_action: str) -> WebInputError:
    if isinstance(error, _next_action.ActionableError):
        return WebInputError(error.reason, next_action=error.next_action)
    # `ActionableError`の派生でない下位層の例外（Git同期の`GitSyncError`・`RebaseInProgressError`など）も、
    # `next_action`属性を持つ場合は発生源の案内を引き継ぐ。
    own_next_action = getattr(error, "next_action", None)
    if isinstance(own_next_action, str) and own_next_action.strip():
        return WebInputError(str(error), next_action=own_next_action)
    return WebInputError(str(error), next_action=next_action)


def validate_filename(filename: str, base_dir: pathlib.Path) -> pathlib.Path:
    try:
        return _wi_filenames.validate_filename(filename, base_dir)
    except SystemExit as error:
        raise WebInputError(
            f"不正なファイル名です: {filename}", next_action="パス区切りを含まないMarkdownのファイル名を指定する"
        ) from error
