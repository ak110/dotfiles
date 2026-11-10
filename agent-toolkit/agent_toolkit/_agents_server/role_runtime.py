"""起動時のコード版と後から読む役割資源の版を、起動前の失敗診断へ対応付ける。"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
from collections.abc import Callable

from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._common.plugin_roots import PluginRootError


def manifest_version(root: pathlib.Path) -> str | None:
    """診断用の版を読み、取得できない場合は不明として元の検査を維持する。"""
    try:
        data = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except OSError, UnicodeError, ValueError:
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) and version else None


@dataclasses.dataclass(frozen=True)
class RuntimeCode:
    """コードを読み込んだ起動時点のrootと版。資源の更新後に読み直して代用しない。"""

    root: pathlib.Path
    version: str | None

    @classmethod
    def capture(cls, root: pathlib.Path) -> RuntimeCode:
        """サーバーの初期化で実装側の実体を一度確定する。"""
        root = root.resolve()
        return cls(root, manifest_version(root))

    def check_role[T](self, stage: str, document: pathlib.Path, operation: Callable[[], T]) -> T:
        """役割検査の失敗に版を対応付け、異なる場合は新プロセスからの回復を案内する。"""
        try:
            return operation()
        except (ActionableError, OSError, UnicodeError, ValueError) as error:
            # 導入資源の不足は候補別の生成・復旧処理が扱うため、元の例外型を保つ。
            if isinstance(error, PluginRootError):
                raise
            resource_root = document.parent.parent
            version = manifest_version(resource_root)
            context = (
                f"起動前の{stage}で失敗した。委譲先はまだ起動していない。"
                f"稼働実装: root={str(self.root)!r} version={self.version!r}。"
                f"役割資源: root={str(resource_root)!r} version={version!r}。"
            )
            action = error.next_action if isinstance(error, ActionableError) else "元の文書診断を直して同じstartを再実行する"
            if self.version is not None and version is not None and self.version != version:
                context += "稼働実装と役割資源の版相違を確認した。"
                if all(re.fullmatch(r"\d+\.\d+\.\d+", value) for value in (self.version, version)) and tuple(
                    map(int, self.version.split("."))
                ) < tuple(map(int, version.split("."))):
                    context += "古い稼働実装が更新後の役割資源を検査している。"
                # 版差だけで文書の正しさまで断定せず、新しい同版の組で元診断を再確認する。
                action = (
                    "ホストのMCP再接続でコードを更新し、同じstart入力を再発行する。"
                    "再接続手段がない場合は、既存の自由本文delegateを新しいプロセスから役割起動する中継として使う。"
                    "原要求・入力・全体作業契約・担当識別子と結果の受領責任を維持する。"
                    "配下の実行中・結果未回収・再開可能を確認する回復手順は"
                    "agent-toolkit:delegation/references/wait-anomalies-failures.md「役割資源との版相違」に従う。"
                    "新しい同版の組でも失敗する場合は元診断の入力・文書を直す"
                )
            raise ActionableError(f"{context}元診断: {error}", next_action=action) from error
