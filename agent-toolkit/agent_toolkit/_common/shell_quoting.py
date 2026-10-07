"""シェル文字列を引用とエスケープの状態を保ちながら1文字ずつ走査する処理。"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class QuotingScanner:
    """引用とエスケープの状態を保ちながらシェル文字列を1文字ずつ走査する。

    `consume_quoted`が真を返した位置は、エスケープ指定・エスケープされた文字・
    引用の内側のいずれかに属し、その位置まで`index`が進む。偽を返した位置は
    引用の外側にあり、呼び出し側が固有の解釈を加えて`index`を進める。
    引用の開始は呼び出し側が判定し、`enter_quote`で状態へ反映する。
    走査を終えた時点で`quote`が`None`でない場合、入力の引用は閉じていない。
    """

    text: str
    index: int = 0
    quote: str | None = None
    escaped: bool = False

    def consume_quoted(self) -> bool:
        """現在位置がエスケープまたは引用に属する場合、位置を進めて真を返す。"""
        char = self.text[self.index]
        if self.escaped:
            self.escaped = False
            self.index += 1
            return True
        if char == "\\" and self.quote != "'":
            self.escaped = True
            self.index += 1
            return True
        if self.quote is not None:
            if char == self.quote:
                self.quote = None
            self.index += 1
            return True
        return False

    def enter_quote(self, quote: str) -> None:
        """引用の開始位置で呼び、引用状態へ入って位置を進める。"""
        self.quote = quote
        self.index += 1
