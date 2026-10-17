"""backendが報告した新規会話識別子を、永続化と共有一覧の更新前に検収する。"""

from collections.abc import Callable, Mapping

from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._common import session_registry
from agent_toolkit._common.next_action import ActionableError

IdentityValidator = Callable[[str, str | None], None]


class DuplicateSessionIdentityError(ActionableError):
    """候補の可用性と独立して起動を拒否する会話識別子の重複。"""


class _RegistryValidator:
    def __init__(self, sessions: Mapping[str, object], root: str | None, retained: tuple[Mapping[str, object], ...]) -> None:
        self.sessions = sessions
        self.root = root
        self.retained = retained

    def __call__(self, session_id: str, expected_session_id: str | None = None) -> None:
        self._validate(session_id, expected_session_id, None)

    async def validate_async(self, session_id: str, expected_session_id: str | None) -> None:
        resolution = None
        if session_id != expected_session_id:
            resolution = await run_file_operation(session_registry.resolve, session_id)
        self._validate(session_id, expected_session_id, resolution)

    def _validate(
        self, session_id: str, expected_session_id: str | None, resolution: session_registry.SessionResolution | None
    ) -> None:
        if expected_session_id == session_id:
            return
        category = None
        if session_id == self.root:
            category = "root session"
        elif session_id in self.sessions:
            category = "active or retained session"
        elif any(session_id in collection for collection in self.retained):
            category = "stopped or expired session"
        else:
            if resolution is None:
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


async def validate_identity(validator: IdentityValidator, session_id: str, expected_session_id: str | None) -> None:
    """登録簿をworkerで読み、共有一覧の重複をloopで最終確認する。"""
    if isinstance(validator, _RegistryValidator):
        await validator.validate_async(session_id, expected_session_id)
    else:
        validator(session_id, expected_session_id)


def identity_validator(
    sessions: Mapping[str, object],
    root_session_id: str | None,
    *retained: Mapping[str, object],
) -> IdentityValidator:
    """共有一覧・保持状態・登録簿との重複を判定する関数を返す。"""
    return _RegistryValidator(sessions, root_session_id, retained)
