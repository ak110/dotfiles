from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class QuotingScanner:
    text: str
    index: int = 0
    quote: str | None = None
    escaped: bool = False

    def consume_quoted(self) -> bool:
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
        self.quote = quote
        self.index += 1
