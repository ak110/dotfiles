"""保存記録のAPI相当額に使う、基準日付きのUSD/100万トークン単価。

請求書の支払額とは区別する。価格はこの表だけに置き、CLIと画面は同じ解決処理を使う。
ClaudeのTTLが記録に無いcache creationは、1時間単価で見積もる。
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

PRICE_DATE = "2026-10-21"
OPENAI_SOURCE = "https://developers.openai.com/api/docs/pricing"
CLAUDE_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"


@dataclasses.dataclass(frozen=True)
class ModelPrice:
    """入力・キャッシュ書込・読取・出力の順の単価と、その出所。"""

    rates: tuple[float, float, float, float]
    source: str
    long_after: int | None = None
    long_rates: tuple[float, float, float, float] | None = None
    short_write_rate: float | None = None


def _openai(input_rate: float, read_rate: float, output_rate: float, *, source: str = OPENAI_SOURCE) -> ModelPrice:
    """272K超では入力・キャッシュが2倍、出力が1.5倍となるモデルの単価を組む。"""
    write_rate = input_rate * 1.25
    return ModelPrice(
        (input_rate, write_rate, read_rate, output_rate),
        source,
        272_000,
        (input_rate * 2, write_rate * 2, read_rate * 2, output_rate * 1.5),
    )


def _claude(input_rate: float, read_rate: float, output_rate: float) -> ModelPrice:
    """Claudeの1時間書込と、明記された5分書込の単価を持つ。"""
    return ModelPrice((input_rate, input_rate * 2, read_rate, output_rate), CLAUDE_SOURCE, short_write_rate=input_rate * 1.25)


MODEL_PRICES = {
    "gpt-6-astra": _openai(10, 1, 50),
    "gpt-6.1-sol": _openai(2, 0.10, 10),
    "gpt-6-sol": _openai(2, 0.20, 10, source="https://developers.openai.com/api/docs/models/gpt-6-sol"),
    "gpt-6-luna": _openai(0.10, 0.01, 0.50),
    "gpt-5.6-sol": _openai(4, 0.40, 20),
    "gpt-5.6-terra": _openai(2, 0.20, 12, source="https://developers.openai.com/api/docs/models/gpt-5.6-terra"),
    "gpt-5.6-luna": _openai(0.20, 0.02, 1.20, source="https://developers.openai.com/api/docs/models/gpt-5.6-luna"),
    "claude-fable-5-1": _claude(10, 0.25, 50),
    "claude-mythos-5-1": _claude(10, 0.25, 50),
    "claude-opus-5-5": _claude(4, 0.20, 20),
    "claude-sonnet-5-5": _claude(2, 0.10, 10),
    "claude-haiku-5-5": ModelPrice((0.10, 0.20, 0.01, 0.50), CLAUDE_SOURCE, 100_000, (0.50, 1, 0.05, 2.50), 0.125),
    **{
        name: _claude(5, 0.50, 25)
        for name in ("claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5")
    },
    "claude-sonnet-5": _claude(2, 0.20, 10),
    **{name: _claude(3, 0.30, 15) for name in ("claude-sonnet-4-6", "claude-sonnet-4-5")},
    "claude-haiku-4-5": _claude(1, 0.10, 5),
}


TOKEN_COMPONENTS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def response_price(model: str | None, tokens: dict[str, int], *, short_write_tokens: int = 0) -> float | None:
    """応答の入力量から区分を選び、単価不明ではNoneを返す。"""
    name = model or ""
    if name.startswith("claude-"):
        name = re.sub(r"-\d{8}$", "", name.removesuffix("[1m]"))
    price = MODEL_PRICES.get(name)
    if price is None:
        return None
    prompt_tokens = sum(tokens.get(key, 0) for key in TOKEN_COMPONENTS[:3])
    long_context = price.long_after is not None and prompt_tokens > price.long_after
    rates = price.long_rates if long_context and price.long_rates is not None else price.rates
    amount = sum(tokens.get(key, 0) * rate for key, rate in zip(TOKEN_COMPONENTS, rates, strict=True))
    if short_write_tokens and price.short_write_rate is not None:
        short_rate = price.short_write_rate
        if long_context:
            short_rate *= rates[0] / price.rates[0]
        amount += min(short_write_tokens, tokens.get("cache_creation_input_tokens", 0)) * (short_rate - rates[1])
    return amount / 1_000_000


def pricing_metadata() -> dict[str, Any]:
    """出所・基準日・区分と、表示へ添える算出式を返す。"""
    return {
        "as_of": PRICE_DATE,
        "currency": "USD",
        "sources": sorted({price.source for price in MODEL_PRICES.values()}),
        "processing": "standard API equivalent",
        "formula": (
            "Claude cost-stateの累積額を優先し、未収録応答は各トークン成分×単価/100万で加算する。"
            "子孫は物理記録ごとに1回だけ数え、親cost-state内のClaudeサブエージェント分を重ねない。"
            "単価不明は既知額から分ける。"
        ),
        "cache_write_assumption": "TTL不明のClaudeは1時間、Codexは30分の書込単価を使う。",
    }
