"""自動生成する本文へXML形式の配送境界を付与する共通モジュール。

本リポジトリが生成してコーディングエージェントの会話文脈またはsystem promptへ入る本文は、
生成主体、種別および配送単位を属性に持つXML要素で囲む。本文に開始タグが現れても、
最初の開始タグと最後の同名終了タグから配送境界を確定できる。

hookの通知、process-loopが渡す追加指示、agent間配送、機械が生成してユーザー入力欄へ入る本文の
いずれもこの関数を経由する。層の順序で最も前にある`_common`へ置くことで、後続の層のどの呼び出し元からも
同じ包装を1つの実装で適用できる。
"""

import contextlib
import functools
import pathlib
import re
from collections.abc import Generator, Mapping
from typing import NamedTuple
lazy from xml.sax.saxutils import quoteattr

AUTO_INSERTED_ELEMENT = "atk-auto"
LEGACY_AUTO_INSERTED_ELEMENT = "agent-toolkit-auto-inserted"
LEGACY_HOOK_MESSAGE_ELEMENT = "agent-toolkit-hook-message"
AUTO_ELEMENTS = (AUTO_INSERTED_ELEMENT, LEGACY_AUTO_INSERTED_ELEMENT, LEGACY_HOOK_MESSAGE_ELEMENT)
"""自動挿入の本文として認識する要素名。生成は現行の`atk-auto`だけを使い、旧形式は過去の記録と会話の認識に残す。"""
AUTO_ELEMENT_NAME_PATTERN = "|".join(re.escape(element) for element in AUTO_ELEMENTS)
"""`AUTO_ELEMENTS`のいずれかの要素名に一致する正規表現（グループを持たない選択）。"""
FORWARDED_USER_INPUT_ELEMENT = "forwarded-user-input"
"""自動生成本文の内側で、ユーザー自身が入力した範囲を囲む要素。

受信側はこの要素の内側だけをユーザーの発話として扱う。配送する手段ごとに別の綴りを持つと、
受信側の判定が配送する手段によって分かれるため、要素名を本モジュールで1つに保つ。
"""


DELEGATE_NOTICE_HEADING = "委譲を受けた担当であることの通知"
_CLOSING_AUTO = f"</{AUTO_INSERTED_ELEMENT}>"
_AUTO_OPENING = re.compile(rf"<{AUTO_INSERTED_ELEMENT}\b[^>]*>")


def _auto_header(text: str) -> tuple[dict[str, str], str] | None:
    if opening := _AUTO_OPENING.match(text):
        attributes = dict(re.findall(r'([\w-]+)="([^"]*)"', opening[0]))
        return attributes, text[opening.end() :]
    return None


def _auto_kind(attributes: Mapping[str, str]) -> tuple[str | None, str | None]:
    return attributes.get("source"), attributes.get("kind")


def _startup_message(text: str, source: str, kind: str) -> str | None:
    parsed = _auto_header(text)
    if parsed is None or _auto_kind(parsed[0]) != (source, kind) or not parsed[1].endswith(_CLOSING_AUTO):
        return None
    return parsed[1].removesuffix(_CLOSING_AUTO).strip() or None


def delegation_delivery_body(text: str) -> str | None:
    text = text.strip()
    parsed = _auto_header(text)
    if parsed is not None and _auto_kind(parsed[0]) == ("agent-toolkit", "plugin-resources"):
        _, closing, rest = parsed[1].partition(_CLOSING_AUTO)
        if not closing:
            return None
        text = rest.lstrip()
    delivery, boundary, suffix = text.rpartition(f"{_CLOSING_AUTO}\n\n")
    if boundary and (advice := _startup_message(suffix, "agent-toolkit", "advice-request")) and _CLOSING_AUTO not in advice:
        text = delivery + _CLOSING_AUTO
    return _startup_message(text, "agents-server", "delivery")


def has_startup_goal(text: str) -> bool:
    text = text.strip()
    return any(_startup_message(text, source, "goal") is not None for source in ("process-loop", "run-skill"))


def has_delegation_launch_prompt(text: str) -> bool:
    parsed = _auto_header(text.lstrip())
    return parsed is not None and _auto_kind(parsed[0]) in {
        ("agent-toolkit", "delegate"),
        ("agent-toolkit", "explore"),
        ("agent-toolkit", "shell"),
        ("agent-toolkit", "write"),
    }


@functools.cache
def delegation_notice_text() -> str | None:
    playbook = pathlib.Path(__file__).resolve().parents[2] / "skills" / "atk-mode" / "playbooks" / "subagent.md"
    try:
        text = playbook.read_text(encoding="utf-8")
    except OSError, UnicodeError:
        return None
    _, heading, rest = text.partition(f"\n## {DELEGATE_NOTICE_HEADING}\n")
    return rest.partition("\n## ")[0].strip() if heading else None


def opening_tag(element: str, attributes: Mapping[str, str]) -> str:
    """XML配送境界の開始タグを返す。"""
    serialized = "".join(f" {name}={quoteattr(value)}" for name, value in attributes.items())
    return f"<{element}{serialized}>"


def xml_message(element: str, body: str, attributes: Mapping[str, str]) -> str:
    """自動生成メッセージへXML配送境界を付与する。"""
    return f"{opening_tag(element, attributes)}\n{body}\n</{element}>"


def starts_with_auto_element(text: str) -> bool:
    """本文が先頭の空白を除いて自動挿入の要素（`AUTO_ELEMENTS`）の開始タグで始まるかを返す。"""
    return text.lstrip().startswith(tuple(f"<{element}" for element in AUTO_ELEMENTS))


def auto_message(body: str, *, source: str, kind: str, attributes: Mapping[str, str] | None = None) -> str:
    """自動挿入本文へ共通の境界と出所を付ける。"""
    extras = {name: value for name, value in (attributes or {}).items() if name not in {"source", "kind"}}
    text = xml_message(AUTO_INSERTED_ELEMENT, body, {"source": source, "kind": kind, **extras})
    if _recordings:
        _recordings[-1].append(GeneratedMessage(text, source, kind, body))
    return text


class GeneratedMessage(NamedTuple):
    """`auto_message`が生成した包装済みの本文と、その生成元・種別・包装前の本文。"""

    text: str
    source: str
    kind: str
    body: str


# 記録中の範囲ごとのリスト（入れ子では最も内側へ記録する）。範囲外では記録せず、
# 常駐プロセス（agents_serverなど）で生成のたびに増え続けないようにする。
_recordings: list[list[GeneratedMessage]] = []


@contextlib.contextmanager
def record_generated() -> Generator[list[GeneratedMessage]]:
    """範囲内で`auto_message`が生成した本文を、生成元・種別・包装前の本文とともに記録する。

    生成済みの本文から包装を字面で切り出すと、本文が引用した開始・終了タグを配送境界と取り違える。
    生成時の値を保持すれば、引用の内容によらず本文の全文と生成元を区別できる。
    """
    recorded: list[GeneratedMessage] = []
    _recordings.append(recorded)
    try:
        yield recorded
    finally:
        _recordings.pop()
