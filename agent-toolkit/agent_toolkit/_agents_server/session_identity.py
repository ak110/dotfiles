"""backendが報告した新規会話識別子を、永続化と共有一覧の更新前に検収する。"""

from collections.abc import Callable, Mapping

from agent_toolkit._common import session_registry
from agent_toolkit._common.next_action import ActionableError

IdentityValidator = Callable[[str, str | None], None]


class DuplicateSessionIdentityError(ActionableError):
    """候補の可用性と独立して起動を拒否する会話識別子の重複。"""


def identity_validator(
    sessions: Mapping[str, object],
    root_session_id: str | None,
    *retained: Mapping[str, object],
) -> IdentityValidator:
    """共有一覧・保持状態・登録簿との重複を判定する関数を返す。"""

    def validate(session_id: str, expected_session_id: str | None = None) -> None:
        if expected_session_id == session_id:
            return
        category = None
        if session_id == root_session_id:
            category = "root session"
        elif session_id in sessions:
            category = "active or retained session"
        elif any(session_id in collection for collection in retained):
            category = "stopped or expired session"
        else:
            resolution = session_registry.resolve(session_id)
            if resolution.state in {session_registry.Resolution.RUNNING, session_registry.Resolution.TERMINAL}:
                category = f"registered {resolution.state.value} session"
        if category is not None:
            raise DuplicateSessionIdentityError(
                f"duplicate session_id: {session_id}; existing={category}; "
                "委譲先CLIが起動元の会話識別子を継承した可能性があります",
                next_action=(
                    "起動元の会話contextの除去を確認してagents_serverを再起動し、`start`を再実行する。"
                    "既存sessionの結果は`atk agents wait`で回収する"
                ),
            )

    return validate
