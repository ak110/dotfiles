"""添付の保存と公開操作を扱う課題。"""

import pathlib


class Attachments:
    """添付名と保存パスを管理する。"""

    def __init__(self, root: str) -> None:
        self.root = pathlib.PurePosixPath(root)
        self.links: dict[str, str] = {}

    def reference(self, name: str) -> str:
        """参照パスを返す。"""
        return str(self.root / name)

    def associate(self, name: str, owner: str) -> str:
        """添付を対象へ関連付ける。"""
        self.links[name] = owner
        return name

    def delete(self, name: str) -> bool:
        """添付の関連付けを削除する。"""
        return self.links.pop(name, None) is not None
