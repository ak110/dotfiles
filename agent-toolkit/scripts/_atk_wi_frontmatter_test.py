"""frontmatterパーサー・直列化モジュールのテスト。"""

import typing

import _atk_wi_frontmatter as frontmatter
import pytest
import yaml


class TestParseFrontmatter:
    """parse_frontmatterの契約テスト。"""

    def test_parse_frontmatter_returns_none_without_delimiter(self) -> None:
        """frontmatter区切りが無い場合Noneを返す。"""
        result = frontmatter.parse_frontmatter("target_repo: example\n本文")
        assert result is None

    def test_parse_frontmatter_returns_none_on_yaml_syntax_error(self) -> None:
        """YAML構文が不正な場合Noneを返す。"""
        result = frontmatter.parse_frontmatter("---\ninvalid: [unterminated\n---\n本文")
        assert result is None

    def test_parse_frontmatter_returns_none_when_top_level_is_not_mapping(self) -> None:
        """トップレベルがマッピングでない場合Noneを返す。"""
        result = frontmatter.parse_frontmatter("---\n- item1\n- item2\n---\n本文")
        assert result is None

    def test_parse_frontmatter_returns_empty_dict_for_empty_frontmatter_without_body(self) -> None:
        """`---\\n---\\n`（本文なし）が空dictと空文字列本文として解析できること。"""
        result = frontmatter.parse_frontmatter("---\n---\n")
        assert result is not None
        data, body = result
        assert data == {}
        assert body == ""

    def test_parse_frontmatter_returns_empty_dict_for_empty_frontmatter_with_body(self) -> None:
        """`---\\n---\\n本文`（本文あり）が空dictと本文として解析できること。"""
        result = frontmatter.parse_frontmatter("---\n---\n本文のみ")
        assert result is not None
        data, body = result
        assert data == {}
        assert body == "本文のみ"

    def test_parse_frontmatter_returns_none_when_top_level_is_yaml_null(self) -> None:
        """`---\\nnull\\n---\\n`・`---\\n~\\n---\\n`（トップレベルがYAML null）は、
        空frontmatterとは区別してNoneを返す。"""
        result_null = frontmatter.parse_frontmatter("---\nnull\n---\n本文")
        assert result_null is None
        result_tilde = frontmatter.parse_frontmatter("---\n~\n---\n本文")
        assert result_tilde is None

    def test_parse_frontmatter_preserves_legacy_queue_schedule_as_nested_value(self) -> None:
        """legacyの`queue_schedule`を未知の入れ子値として読み取れる。"""
        text = """---
target_repo: example
queue_schedule:
  type: normal
  carry_count: 2
---
本文"""
        result = frontmatter.parse_frontmatter(text)
        assert result is not None
        data, body = result
        assert isinstance(data.get("queue_schedule"), dict)
        assert data["queue_schedule"]["type"] == "normal"
        assert data["queue_schedule"]["carry_count"] == 2
        assert body == "本文"

    def test_parse_frontmatter_matches_for_python_and_libyaml_loader_bases(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """純Python実装とlibyaml実装で字面保持と`queue_schedule`の型が一致する。"""
        text = """---
bool_value: true
int_value: 42
float_value: 1.5
null_value: null
timestamp_value: 2024-01-01T12:00:00+09:00
queue_schedule:
  carry_count: 2
  enabled: true
---
本文"""
        loader_bases: list[type[yaml.SafeLoader]] = [yaml.SafeLoader]
        c_safe_loader = getattr(yaml, "CSafeLoader", None)
        if c_safe_loader is not None:
            loader_bases.append(c_safe_loader)
        results: list[tuple[dict[str, typing.Any], str] | None] = []

        for loader_base in loader_bases:
            literal_scalar_loader = typing.cast(typing.Any, type("LiteralScalarLoader", (loader_base,), {}))

            def construct_literal_scalar(loader: typing.Any, node: yaml.Node) -> str:
                assert isinstance(node, yaml.ScalarNode)
                return typing.cast(str, loader.construct_scalar(node))

            for tag in (
                "tag:yaml.org,2002:bool",
                "tag:yaml.org,2002:int",
                "tag:yaml.org,2002:float",
                "tag:yaml.org,2002:null",
                "tag:yaml.org,2002:timestamp",
            ):
                literal_scalar_loader.add_constructor(tag, construct_literal_scalar)
            monkeypatch.setattr(frontmatter, "_SafeLoaderBase", loader_base)
            monkeypatch.setattr(frontmatter, "_LiteralScalarLoader", literal_scalar_loader)
            results.append(frontmatter.parse_frontmatter(text))

        assert results and all(result == results[0] for result in results)
        assert results[0] == (
            {
                "bool_value": "true",
                "int_value": "42",
                "float_value": "1.5",
                "null_value": "null",
                "timestamp_value": "2024-01-01T12:00:00+09:00",
                "queue_schedule": {"carry_count": 2, "enabled": True},
            },
            "本文",
        )

    def test_parse_frontmatter_preserves_depends_on_sequence(self) -> None:
        """トップレベルの`depends_on`を文字列配列として保持する。"""
        result = frontmatter.parse_frontmatter("---\ndepends_on:\n  - a.md\n  - b.md\n---\n本文")
        assert result is not None
        data, body = result
        assert data["depends_on"] == ["a.md", "b.md"]
        assert body == "本文"

    def test_parse_frontmatter_keeps_iso8601_timestamp_value_as_literal_string(self) -> None:
        """`created: 2024-01-01T12:00:00+09:00`が`datetime`化されず、元の字面のまま`str`で返る。"""
        text = "---\ncreated: 2024-01-01T12:00:00+09:00\n---\n本文"
        result = frontmatter.parse_frontmatter(text)
        assert result is not None
        data, _ = result
        created_value = data.get("created")
        assert isinstance(created_value, str)
        assert created_value == "2024-01-01T12:00:00+09:00"

    def test_parse_frontmatter_keeps_bool_and_null_like_scalars_as_literal_string(self) -> None:
        """`yes`/`no`/`on`/`off`/`null`/`~`風の値が真偽値・Noneへ変換されず、元の字面のまま返る。"""
        test_cases = [
            ("flag: yes\n", "yes"),
            ("flag: no\n", "no"),
            ("flag: on\n", "on"),
            ("flag: off\n", "off"),
            ("value: null\n", "null"),
            ("value: ~\n", "~"),
        ]
        for frontmatter_source, expected_value in test_cases:
            text = f"---\n{frontmatter_source}---\n本文"
            result = frontmatter.parse_frontmatter(text)
            assert result is not None
            data, _ = result
            key = "flag" if "flag" in frontmatter_source else "value"
            assert isinstance(data.get(key), str), f"Expected str for {frontmatter_source}, got {type(data.get(key))}"
            assert data.get(key) == expected_value

    def test_parse_frontmatter_preserves_unknown_key_nested_mapping_and_sequence_structure(self) -> None:
        """`queue_schedule`以外の未知キーが持つ入れ子マッピング・シーケンスの構造が、
        Pythonの`repr`風文字列へ変換されず、`dict`・`list`のまま保たれる。"""
        text = """---
metadata:
  nested_map:
    key: value
  nested_list:
    - a
    - b
---
本文"""
        result = frontmatter.parse_frontmatter(text)
        assert result is not None
        data, _ = result
        metadata = data.get("metadata")
        assert isinstance(metadata, dict)
        nested_map = metadata.get("nested_map")
        assert isinstance(nested_map, dict)
        assert nested_map.get("key") == "value"
        nested_list = metadata.get("nested_list")
        assert isinstance(nested_list, list)
        assert nested_list == ["a", "b"]

    def test_parse_frontmatter_does_not_mutate_body_leading_or_trailing_bytes(self) -> None:
        """本文の先頭・末尾のバイトが一切変化しない。"""
        original_body = "  先頭空白\n本文\n末尾改行\n"
        text = f"---\ntarget_repo: example\n---\n{original_body}"
        result = frontmatter.parse_frontmatter(text)
        assert result is not None
        _, body = result
        assert body == original_body

    def test_serialize_frontmatter_preserves_insertion_order(self) -> None:
        """挿入順が保持される。"""
        data = {"target_repo": "example", "type": "normal", "custom_key": "value"}
        body = "本文"
        result = frontmatter.serialize_frontmatter(data, body)
        # PyYAML 6以上はdict挿入順を保持するため、行順で確認
        lines = result.split("\n")
        assert lines[0] == "---"
        assert "target_repo:" in lines[1]
        assert "type:" in lines[2]
        assert "custom_key:" in lines[3]
        assert lines[4] == "---"
        assert lines[5] == "本文"

    def test_serialize_frontmatter_round_trips_through_parse_frontmatter(self) -> None:
        """parse → serialize → parseで内容が保持される。"""
        original_text = """---
target_repo: example
type: normal
source: feedback
---
本文内容"""
        parsed = frontmatter.parse_frontmatter(original_text)
        assert parsed is not None
        data, body = parsed
        serialized = frontmatter.serialize_frontmatter(data, body)
        reparsed = frontmatter.parse_frontmatter(serialized)
        assert reparsed is not None
        reparsed_data, reparsed_body = reparsed
        assert reparsed_data == data
        assert reparsed_body == body

    def test_round_trip_preserves_body_across_repeated_serialize_calls(self) -> None:
        """反復`serialize_frontmatter`呼び出しでも本文が直接保持される。"""
        data = {"target_repo": "example", "type": "normal"}
        body = "本文内容"
        serialized1 = frontmatter.serialize_frontmatter(data, body)
        parsed1 = frontmatter.parse_frontmatter(serialized1)
        assert parsed1 is not None
        _, body1 = parsed1
        assert body1 == body

        serialized2 = frontmatter.serialize_frontmatter(data, body1)
        parsed2 = frontmatter.parse_frontmatter(serialized2)
        assert parsed2 is not None
        _, body2 = parsed2
        assert body2 == body

    @pytest.mark.parametrize(
        "frontmatter_source",
        [
            "created: 2024-01-01T12:00:00+09:00\n",
            "created: 2024-01-01T12:00:00Z\n",
            "flag: yes\n",
            "flag: no\n",
            "flag: on\n",
            "flag: off\n",
            "value: null\n",
            "value: ~\n",
            "code: 0042\n",
            "nested:\n  key: value\n  list:\n    - a\n    - b\n",
        ],
    )
    def test_round_trip_over_fixed_scalar_shapes_preserves_literal_values(self, frontmatter_source: str) -> None:
        """観測済みの値形態（timestamp・真偽値風・null風・先頭ゼロ付き数値・未知キーの入れ子構造）を
        固定フィクスチャとして、parse前後で字面同値であることを恒常的に検証する。"""
        text = f"---\n{frontmatter_source}---\n本文"
        parsed = frontmatter.parse_frontmatter(text)
        assert parsed is not None
        data, body = parsed
        serialized = frontmatter.serialize_frontmatter(data, body)
        reparsed = frontmatter.parse_frontmatter(serialized)
        assert reparsed is not None
        reparsed_data, _ = reparsed
        # 値が字面のまま保持されたことを確認
        for key, value in reparsed_data.items():
            original_value = data[key]
            assert value == original_value
