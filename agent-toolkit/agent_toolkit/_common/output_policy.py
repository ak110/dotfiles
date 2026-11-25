import functools
import json
import pathlib
from typing import TypedDict, cast


class Acceptance(TypedDict):
    max_chars: int
    includes: list[str]
    excludes: list[str]


class OutputPolicy(TypedDict):
    suppress_repeats: bool
    show_total_with_delta: bool
    omit_defaults: bool
    full_guidance_once: bool
    search_guidance: str
    wait_improvement_guidance: str
    send_guidance: str
    send_repeat: str
    acceptance: dict[str, Acceptance]


@functools.cache
def rules() -> OutputPolicy:
    path = pathlib.Path(__file__).with_name("output-policy.ts")
    source = path.read_text(encoding="utf-8").strip()
    # Claude Function hooksはコードだけをimportできるため、定義はJSON互換のTSとして共有する。  # pyfltr: allow-comment
    return cast(OutputPolicy, json.loads(source.removeprefix("export default ").removesuffix(";")))
