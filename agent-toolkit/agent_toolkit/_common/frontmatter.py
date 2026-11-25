import re
import typing

import yaml

if typing.TYPE_CHECKING:
    _SafeLoaderBase = yaml.SafeLoader
else:
    _SafeLoaderBase = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class _LiteralScalarLoader(_SafeLoaderBase):  # pylint: disable=too-many-ancestors
    """スカラーノードの字面をそのまま`str`として構築する。 pyfltr: allow-comment

    bool・int・float・null・timestampの各タグで標準的に使うコンストラクターを、
    `construct_scalar`が返すノード文字列
    （引用符解決後の生のYAML字面）へ差し替える。マッピング・シーケンスの構築は
    選択した安全な基底ローダーの標準動作を維持するため、入れ子構造そのものは保持される。
    """


def _construct_literal_scalar(loader: yaml.SafeLoader, node: yaml.Node) -> str:
    assert isinstance(node, yaml.ScalarNode)
    return loader.construct_scalar(node)


for _tag in (
    "tag:yaml.org,2002:bool",
    "tag:yaml.org,2002:int",
    "tag:yaml.org,2002:float",
    "tag:yaml.org,2002:null",
    "tag:yaml.org,2002:timestamp",
):
    _LiteralScalarLoader.add_constructor(_tag, _construct_literal_scalar)


_SIMPLE_LINE = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*): ([^\s\-?:,\[\]{}#&*!|>'\"%@`][^\t]*)")


def _simple_mapping(source: str, typed_keys: frozenset[str]) -> dict[str, str] | None:
    data: dict[str, str] = {}
    for line in source.split("\n"):
        match = _SIMPLE_LINE.fullmatch(line)
        if match is None:
            return None
        key, value = match.groups()
        if key in typed_keys or value != value.rstrip() or ": " in value or " #" in value or value.endswith(":"):
            return None
        data[key] = value
    return data


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_frontmatter(text: str, *, typed_keys: frozenset[str] = frozenset()) -> tuple[dict[str, typing.Any], str] | None:
    r"""先頭のYAML frontmatterをマッピングとして解析し、本文とともに返す。 pyfltr: allow-comment

    frontmatter区切り（先頭`---\\n`と閉じ`\\n---\\n`）が無い場合、YAML構文が不正な場合、
    トップレベルがマッピングでない場合はNoneを返す。空のfrontmatter（`---\\n---\\n`）は
    空dictとして扱う。
    スカラーは暗黙の型推論を経ない字面のままの文字列とする。`typed_keys`に属する項目だけは
    YAML標準の推論結果を保持し、その項目が存在する入力に限り型付きのローダーで再解析する。
    空のマッピングとYAML nullは区別し、後者はマッピングではないため受理しない。
    本文は区切り直後から末尾までを一切加工せず（先頭改行の除去も末尾改行の追加もせず）返す
    （本文が変化しないことは、`serialize_frontmatter`との往復後の本文文字列を直接比較して確認する）。
    """
    text = text.removeprefix("\ufeff")
    if not text.startswith("---\n"):
        return None
    try:
        end = text.index("\n---\n", 3)
    except ValueError:
        return None
    frontmatter_source = text[4:end]
    if (simple := _simple_mapping(frontmatter_source, typed_keys)) is not None:
        return simple, text[end + len("\n---\n") :]
    try:
        literal = yaml.load(frontmatter_source, Loader=_LiteralScalarLoader)
        typed = (
            yaml.load(frontmatter_source, Loader=_SafeLoaderBase)
            if isinstance(literal, dict) and typed_keys & literal.keys()
            else literal
        )
    except yaml.YAMLError:
        return None
    if not frontmatter_source.strip():
        literal, typed = {}, {}
    if not isinstance(literal, dict) or not isinstance(typed, dict):
        return None
    data = {key: (typed.get(key) if key in typed_keys else value) for key, value in literal.items()}
    body = text[end + len("\n---\n") :]
    return data, body


def serialize_frontmatter(data: typing.Mapping[str, typing.Any], body: str) -> str:
    dumped = yaml.safe_dump(dict(data), sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{dumped}---\n{body}"
