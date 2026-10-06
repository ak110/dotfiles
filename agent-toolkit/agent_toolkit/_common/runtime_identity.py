"""エージェント実行identityの由来と表示を共有する。"""

import re
from dataclasses import dataclass
from typing import Any, Literal

IdentitySource = Literal["requested", "launch_candidate", "observed"]


@dataclass(frozen=True)
class RuntimeIdentity:
    """起動要求・起動候補・ホスト観測値を由来付きで保持する。"""

    engine: str
    model: str
    effort: str
    source: IdentitySource

    @property
    def machine(self) -> str:
        """永続化と比較に使う決定論的な表現を返す。"""
        return f"{self.engine}:{self.model}/{self.effort}"

    def display(self, *, catalog: list[dict[str, Any]] | None = None) -> str:
        """モデルカタログの表示名を優先した人間向け表現を返す。"""
        display_model = self.model
        for item in catalog or ():
            model_id = item.get("model") or item.get("id")
            if model_id != self.model:
                continue
            candidate = item.get("displayName") or item.get("display_name") or item.get("name")
            if isinstance(candidate, str) and candidate:
                display_model = candidate
                break
        if display_model == self.model:
            display_model = _fallback_model_display(self.model)
        return f"{display_model} / {self.effort.capitalize()}"

    def public(self) -> dict[str, str]:
        """API応答へ埋め込める構造化値を返す。"""
        return {
            "engine": self.engine,
            "model": self.model,
            "effort": self.effort,
            "source": self.source,
        }


# Claudeの完全モデルID（`claude-opus-5-5`、日付付きの`claude-haiku-4-5-20251001`など）の系列と版。
_CLAUDE_MODEL_ID = re.compile(r"claude-([a-z]+)-(\d+)-(\d+)(?:-\d{8})?")


def _fallback_model_display(model: str) -> str:
    """完全モデルIDを、カタログ無しでも安定した表示名へ変換する。形の合わないIDはそのまま返す。"""
    parts = model.split("-")
    if len(parts) >= 3 and parts[0].lower() == "gpt":
        return f"GPT-{parts[1]} {' '.join(part.capitalize() for part in parts[2:])}"
    if (matched := _CLAUDE_MODEL_ID.fullmatch(model)) is not None:
        family, major, minor = matched.groups()
        return f"Claude {family.capitalize()} {major}.{minor}"
    return model


def observed_identity(entry: dict[str, Any], runtime: str) -> RuntimeIdentity | None:
    """ホスト記録1件からモデルと推論量の組を取り出し、両方が明記されている場合だけ返す。

    Claude Codeの記録はassistant行の`message.model`と最上位の`effort`に、Codexの記録は最上位の`type`が
    `turn_context`の行の`payload.model`と`payload.effort`に組を持つ（Claude Code 2.1.291とCodex 0.160.1の記録で確認）。
    Claude Codeが合成した応答の行は`effort`がnullのため、組を持たない行として除く。
    """
    if runtime == "codex":
        payload = entry.get("payload")
        if entry.get("type") != "turn_context" or not isinstance(payload, dict):
            return None
        model, effort = payload.get("model"), payload.get("effort")
    elif runtime == "claude":
        message = entry.get("message")
        model = message.get("model") if isinstance(message, dict) else None
        effort = entry.get("effort")
    else:
        return None
    if isinstance(model, str) and model and isinstance(effort, str) and effort:
        return RuntimeIdentity(runtime, model, effort, "observed")
    return None


def identity_observations(records: Any, runtime: str) -> list[tuple[int, RuntimeIdentity]]:
    """行番号とentryを持つ記録列から、順序を保った観測値を返す。"""
    result: list[tuple[int, RuntimeIdentity]] = []
    for fallback_line, record in enumerate(records, start=1):
        entry = getattr(record, "entry", record)
        if not isinstance(entry, dict):
            continue
        identity = observed_identity(entry, runtime)
        if identity is not None:
            result.append((int(getattr(record, "line", fallback_line)), identity))
    return result


def distinct_identities(records: Any, runtime: str) -> list[RuntimeIdentity]:
    """記録中の全観測組を初出順で重複除去して返す。"""
    result: list[RuntimeIdentity] = []
    seen: set[str] = set()
    for _line, identity in identity_observations(records, runtime):
        if identity.machine not in seen:
            seen.add(identity.machine)
            result.append(identity)
    return result


def latest_identity(records: Any, runtime: str, *, before_line: int) -> tuple[int, RuntimeIdentity] | None:
    """指定行以前の直近の観測値を返す。"""
    return next(
        ((line, identity) for line, identity in reversed(identity_observations(records, runtime)) if line <= before_line),
        None,
    )


def co_author_trailer(identity: RuntimeIdentity, *, catalog: list[dict[str, Any]] | None = None) -> str:
    """観測済みidentityからcommit帰属trailerを生成する。

    表示はユーザーが示した例（`Claude Opus 5.5 / High`、`GPT-6.1 Sol / Medium`）の形へそろえる。
    """
    if identity.source != "observed":
        raise ValueError("commit帰属にはホストが観測した実行identityが必要です")
    domain = "openai.com" if identity.engine == "codex" else "anthropic.com" if identity.engine == "claude" else identity.engine
    return f"Co-Authored-By: {identity.display(catalog=catalog)} <noreply@{domain}>"
