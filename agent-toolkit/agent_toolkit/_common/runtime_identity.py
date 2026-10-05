"""エージェント実行identityの由来と表示を共有する。"""

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


def _fallback_model_display(model: str) -> str:
    """完全モデルIDを、カタログ無しでも安定した表示名へ変換する。"""
    parts = model.split("-")
    if len(parts) >= 3 and parts[0].lower() == "gpt":
        return f"GPT-{parts[1]} {' '.join(part.capitalize() for part in parts[2:])}"
    return model


def observed_identity(entry: dict[str, Any], runtime: str) -> RuntimeIdentity | None:
    """ホスト記録1件にmodelとeffortの両方が明記されている場合だけ返す。"""
    payload = entry.get("payload")
    message = entry.get("message")
    containers = [
        payload if isinstance(payload, dict) else {},
        message if isinstance(message, dict) else {},
        entry,
    ]
    if runtime == "codex" and containers[0].get("type") != "turn_context":
        return None
    for container in containers:
        model = container.get("model")
        effort = container.get("effort") or container.get("reasoning_effort") or container.get("reasoningEffort")
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
    """観測済みidentityからcommit帰属trailerを生成する。"""
    if identity.source != "observed":
        raise ValueError("commit帰属にはホストが観測した実行identityが必要です")
    domain = "openai.com" if identity.engine == "codex" else "anthropic.com" if identity.engine == "claude" else identity.engine
    display = identity.display(catalog=catalog) if identity.engine == "codex" else f"{identity.model} / {identity.effort}"
    return f"Co-Authored-By: {display} <noreply@{domain}>"
