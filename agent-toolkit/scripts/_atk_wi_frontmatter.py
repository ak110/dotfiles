"""frontmatterの読み書きを提供する共有モジュール。PyYAMLを使用して標準的なYAML形式をサポート。

このモジュールは他の`_atk_wi_*`モジュールへ依存せず、依存グラフの最下層に置く。
`_atk_wi_common.py`・`_atk_wi_formatters.py`・`_atk_wi_add.py`・`_atk_wi_mutations.py`・
`_atk_wi_common.py`・`_atk_serve_app.py`・`_uwi_scan.py`がこのモジュールから一方向にimportする。
"""

import pathlib
import typing

import yaml

if typing.TYPE_CHECKING:
    _SafeLoaderBase = yaml.SafeLoader
else:
    # libyamlを同梱したPyYAMLではC実装のローダーを基底に選ぶ。
    # 同梱しない配布では純Python実装を基底とし、解析結果は変わらない。
    _SafeLoaderBase = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class _LiteralScalarLoader(_SafeLoaderBase):  # pylint: disable=too-many-ancestors
    """暗黙の型推論を行わず、スカラーノードの字面をそのまま`str`として構築するローダー。

    bool・int・float・null・timestampの各タグに対する既定コンストラクターを、
    型変換済みのPythonオブジェクトではなく`construct_scalar`が返すノード文字列
    （引用符解決後の生のYAML字面）へ差し替える。マッピング・シーケンスの構築は
    選択した安全な基底ローダーの既定を維持するため、入れ子構造そのものは保持される。
    """


def _construct_literal_scalar(loader: yaml.SafeLoader, node: yaml.Node) -> str:
    """暗黙タグにかかわらずスカラーノードの字面を返す。"""
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


def normalize_newlines(text: str) -> str:
    """CRLFとCRをLFへ正規化して返す。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def decode_entry_text(data: bytes) -> str:
    """キュー項目のbytesをUTF-8で復号し、改行をLFへ正規化して返す。

    読み取り時にテキストIOのユニバーサル改行処理を経ないため、Windowsで保存したCRLFの本文が
    frontmatterの区切り照合と読み直した本文との一致判定を通らない。復号の時点で正規化して両者をそろえる。
    保存bytesをそのまま保つ追記経路では、保存する内容の組み立てへ本関数を使わない。
    """
    return normalize_newlines(data.decode("utf-8"))


def write_entry_text(path: pathlib.Path, content: str) -> None:
    """キュー項目の本文をLF改行のUTF-8で保存する。

    テキストモードの既定はプラットフォームの改行へ変換するため、Windowsで保存した本文がCRLFとなり、
    bytesで読み直す経路がfrontmatterを解析できなくなる。本文をLFへ正規化したうえでテキストモードを
    経ずに符号化して書き込み、保存形式を実行環境から独立させる。
    """
    path.write_bytes(normalize_newlines(content).encode("utf-8"))


def parse_frontmatter(text: str) -> tuple[dict[str, typing.Any], str] | None:
    r"""先頭のYAML frontmatterをマッピングとして解析し、本文とともに返す。

    frontmatter区切り（先頭`---\\n`と閉じ`\\n---\\n`）が無い場合、YAML構文が不正な場合、
    トップレベルがマッピングでない場合はNoneを返す。空のfrontmatter（`---\\n---\\n`）は
    空dictとして扱う。
    `queue_schedule`以外の値は`_LiteralScalarLoader`で解析し、暗黙の型推論を経ない
    字面のままの`str`として保持する（マッピング・シーケンスの構造自体はそのまま保つ）。
    `queue_schedule`だけは選択した安全な基底ローダーで別途解析し、`carry_count`等の
    ネイティブ型を保つ。frontmatter全文を2種のローダーでそれぞれ1回ずつ解析する
    （`queue_schedule`は`carry_count`のような整数を必要とするため、字面保持ローダーの
    対象から除く）。
    本文は区切り直後から末尾までを一切加工せず（先頭改行の除去も末尾改行の追加もせず）返す
    （本文が変化しないことは、`serialize_frontmatter`との往復後の本文文字列を直接比較して確認する）。
    """
    if not text.startswith("---\n"):
        return None
    try:
        # 開始区切り末尾の`\n`（index 3）を検索開始位置とする。空frontmatter（`---\n---\n`）では
        # 開始区切りと閉じ区切りが同じ`\n`を共有し、閉じ区切りの一致がindex 3から始まるため、
        # 検索開始位置を4にすると空frontmatterの閉じ区切りを検出できない。
        end = text.index("\n---\n", 3)
    except ValueError:
        return None
    frontmatter_source = text[4:end]
    try:
        literal = yaml.load(frontmatter_source, Loader=_LiteralScalarLoader)
        typed = yaml.load(frontmatter_source, Loader=_SafeLoaderBase)
    except yaml.YAMLError:
        return None
    # `frontmatter_source`が空文字列（または空白のみ）の場合だけ空dictへ正規化する。
    # トップレベルがYAML null（`null`・`~`）の場合も両ローダーは`None`を返すが、
    # この場合は「トップレベルがマッピングでない」契約に従いNoneを返す必要があるため、
    # 空文字列判定と区別する（無条件にNoneを{}へ正規化すると、トップレベルnullを
    # 誤って空dictとして受理してしまう）。
    if not frontmatter_source.strip():
        literal, typed = {}, {}
    if not isinstance(literal, dict) or not isinstance(typed, dict):
        return None
    data = {key: (typed.get("queue_schedule") if key == "queue_schedule" else value) for key, value in literal.items()}
    body = text[end + len("\n---\n") :]
    return data, body


def serialize_frontmatter(data: typing.Mapping[str, typing.Any], body: str) -> str:
    r"""マッピングをYAML frontmatterへ直列化し本文と結合する。

    `sort_keys=False`でdataの挿入順を保持し、`allow_unicode=True`で日本語をエスケープしない。
    本文は受け取った文字列を一切加工せず`---\\n`直後へ連結する（`parse_frontmatter`が返した
    本文をそのまま渡す前提であり、呼び出し側で改行の追加・削除をしない）。
    """
    dumped = yaml.safe_dump(dict(data), sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{dumped}---\n{body}"
