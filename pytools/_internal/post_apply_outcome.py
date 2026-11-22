"""post-applyステップの構造化結果を定義する。"""

from dataclasses import dataclass

CHECK_UPDATE_LOG = "失敗理由を確認し、`update-dotfiles`を再実行する"


@dataclass(frozen=True)
class PostApplyNotice:
    """post-apply完了時に表示する案内を表す。"""

    message: str
    command: str | None = None

    def render(self) -> str:
        """本文にないコマンドだけを次の行へ字下げして示す。"""
        if self.command is None or f"`{self.command}`" in self.message:
            return self.message
        return f"{self.message}\n    {self.command}"


@dataclass(frozen=True)
class PostApplyOutcome:
    changed: bool = False
    notices: tuple[PostApplyNotice, ...] = ()

    recommendations: tuple[str, ...] = ()
    failure: str | None = None
    next_action: str | None = None
    deferred: bool = False

    def __post_init__(self) -> None:
        """失敗理由と次の操作が揃った結果だけを受け取る。"""
        if self.failure is None:
            if self.next_action is not None:
                raise ValueError("次の操作は失敗理由とともに指定する")
            return
        if not isinstance(self.failure, str) or not self.failure.strip():
            raise ValueError("失敗理由には空でない文字列を指定する")
        if not isinstance(self.next_action, str) or not self.next_action.strip():
            raise ValueError("失敗時の次の操作には空でない文字列を指定する")
