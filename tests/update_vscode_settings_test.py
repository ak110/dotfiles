"""update_vscode_settings モジュールのテスト。"""

# `_hostname_color` など、パッケージ内部関数 (先頭 _) を
# テストするため protected-access を全体で許可する。
# pylint: disable=protected-access

import itertools
import json
import math
from pathlib import Path

import pytest

from pytools._internal import update_vscode_settings as mod


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


class TestHostnameColor:
    """_hostname_color のテスト。"""

    def test_returns_valid_hex_color(self) -> None:
        """有効な 7 文字の hex カラーコードを返す。"""
        color = mod._hostname_color(hostname="test-host")
        assert color.startswith("#")
        assert len(color) == 7
        int(color[1:], 16)  # 16 進数として有効であること

    def test_palette_brightness_not_too_dark(self) -> None:
        """パレット全色の加重平均輝度が閾値 140 以上である。"""
        for color in mod._HOST_COLORS:
            r, g, b = _hex_to_rgb(color)
            luminance = 0.299 * r + 0.587 * g + 0.114 * b
            assert luminance >= 140, f"パレット色 {color} が暗すぎる (輝度: {luminance:.1f})"

    def test_color_is_in_palette(self) -> None:
        """複数ホスト名の戻り値が常にパレット内に含まれる。"""
        palette = set(mod._HOST_COLORS)
        for hostname in ["host-a", "host-b", "server-1", "x", "long-hostname-example", "desk", "laptop"]:
            assert mod._hostname_color(hostname=hostname) in palette

    def test_palette_min_distance(self) -> None:
        """パレット内の全ペアの RGB ユークリッド距離が十分に離れている。

        連続 HSL サンプリング時代にユーザーが「区別がつかない」と報告した近接ペアの
        距離は約 23.96 だった。将来パレットを差し替えた際にも、少なくとも閾値 40 を
        下回らないことをパレット自体のサニティーテストとして担保する。
        """
        min_distance = min(math.dist(_hex_to_rgb(a), _hex_to_rgb(b)) for a, b in itertools.combinations(mod._HOST_COLORS, 2))
        assert min_distance >= 40.0, f"パレット内の最小ペア距離 {min_distance:.2f} が閾値 40 を下回っている"

    def test_deterministic(self) -> None:
        """同じホスト名なら同じ色を返す。"""
        first = mod._hostname_color(hostname="stable-host")
        second = mod._hostname_color(hostname="stable-host")
        assert first == second


class TestSettingsPath:
    """_settings_path のテスト。"""

    def test_linux_returns_path_when_vscode_server_exists(self, tmp_path: Path) -> None:
        """Linux: .vscode-server が存在すればパスを返す。"""
        (tmp_path / ".vscode-server").mkdir()
        path = mod._settings_path(is_windows=False, home=tmp_path)
        assert path is not None
        assert ".vscode-server" in str(path)
        assert str(path).endswith("settings.json")

    def test_linux_returns_none_when_vscode_server_missing(self, tmp_path: Path) -> None:
        """Linux: .vscode-server が存在しなければ None を返す。"""
        assert mod._settings_path(is_windows=False, home=tmp_path) is None

    def test_windows_returns_path_when_code_dir_exists(self, tmp_path: Path) -> None:
        """Windows: %APPDATA%/Code が存在すればパスを返す。"""
        (tmp_path / "Code").mkdir()
        path = mod._settings_path(is_windows=True, environ={"APPDATA": str(tmp_path)})
        assert path is not None
        assert "Code" in str(path)

    def test_windows_returns_none_when_appdata_missing(self) -> None:
        """Windows: APPDATA 未設定なら None を返す。"""
        assert mod._settings_path(is_windows=True, environ={}) is None


class TestApply:
    """_apply のマージ動作テスト。"""

    def test_creates_new_file(self, tmp_path: Path) -> None:
        """settings.json が存在しない場合、managed がそのまま出力される。"""
        target = tmp_path / "sub" / "settings.json"
        managed = {"workbench.colorCustomizations": {"activityBar.background": "#aabbcc"}}
        assert mod._apply(managed, target) is True
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["workbench.colorCustomizations"]["activityBar.background"] == "#aabbcc"

    def test_preserves_existing_keys(self, tmp_path: Path) -> None:
        """既存キーが保持される。"""
        target = tmp_path / "settings.json"
        target.write_text(json.dumps({"editor.fontSize": 14}, ensure_ascii=False), encoding="utf-8")
        managed = {"workbench.colorCustomizations": {"activityBar.background": "#aabbcc"}}
        mod._apply(managed, target)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["editor.fontSize"] == 14
        assert result["workbench.colorCustomizations"]["activityBar.background"] == "#aabbcc"

    def test_preserves_other_color_customizations(self, tmp_path: Path) -> None:
        """colorCustomizations 内の他キーが保持される (浅い dict マージ)。"""
        target = tmp_path / "settings.json"
        existing = {"workbench.colorCustomizations": {"titleBar.activeBackground": "#112233"}}
        target.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
        managed = {"workbench.colorCustomizations": {"activityBar.background": "#aabbcc"}}
        mod._apply(managed, target)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["workbench.colorCustomizations"]["titleBar.activeBackground"] == "#112233"
        assert result["workbench.colorCustomizations"]["activityBar.background"] == "#aabbcc"

    def test_list_is_overwritten(self, tmp_path: Path) -> None:
        """list 値は上書きされる (union マージではない)。"""
        target = tmp_path / "settings.json"
        target.write_text(
            json.dumps({"markdown-pdf.styles": ["/old/markdown-pdf.css"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        managed = {"markdown-pdf.styles": ["/new/markdown-pdf.css"]}
        mod._apply(managed, target)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["markdown-pdf.styles"] == ["/new/markdown-pdf.css"]

    def test_legacy_keys_are_removed(self, tmp_path: Path) -> None:
        """legacy_keys で指定したキーは apply 時に削除される。

        Machine scope に過去バージョンが書き込んでいた markdown.styles の
        絶対パスを除去する用途の挙動を保証する。
        """
        target = tmp_path / "settings.json"
        target.write_text(
            json.dumps(
                {
                    "markdown.styles": ["/home/aki/dotfiles/share/vscode/markdown.css"],
                    "editor.fontSize": 14,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        managed = {"workbench.colorCustomizations": {"activityBar.background": "#aabbcc"}}
        changed = mod._apply(managed, target, legacy_keys=("markdown.styles",))
        assert changed is True
        result = json.loads(target.read_text(encoding="utf-8"))
        assert "markdown.styles" not in result
        assert result["editor.fontSize"] == 14
        assert result["workbench.colorCustomizations"]["activityBar.background"] == "#aabbcc"

    def test_legacy_keys_missing_is_noop(self, tmp_path: Path) -> None:
        """legacy_keys で指定したキーが存在しない場合はエラーにならない。"""
        target = tmp_path / "settings.json"
        managed = {"foo": "bar"}
        target.write_text(json.dumps(managed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        assert mod._apply(managed, target, legacy_keys=("markdown.styles",)) is False

    def test_no_change_returns_false(self, tmp_path: Path) -> None:
        """変更がなければ False を返す。"""
        target = tmp_path / "settings.json"
        managed = {"foo": "bar"}
        target.write_text(json.dumps(managed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        assert mod._apply(managed, target) is False

    def test_reads_jsonc_with_comments(self, tmp_path: Path) -> None:
        """JSONC (コメント付き) ファイルをパースしてマージできる。"""
        target = tmp_path / "settings.json"
        jsonc_content = """{
  // エディター設定
  "editor.fontSize": 14,
  /* テーマ */
  "workbench.colorTheme": "Default Dark+",
}"""
        target.write_text(jsonc_content, encoding="utf-8")
        managed = {"workbench.colorCustomizations": {"activityBar.background": "#aabbcc"}}
        mod._apply(managed, target)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["editor.fontSize"] == 14
        assert result["workbench.colorTheme"] == "Default Dark+"
        assert result["workbench.colorCustomizations"]["activityBar.background"] == "#aabbcc"

    def test_reads_jsonc_with_line_comment_only(self, tmp_path: Path) -> None:
        """行コメント (//) のみを含む JSONC ファイルをパースできる。"""
        target = tmp_path / "settings.json"
        target.write_text('{\n  // 行コメント\n  "key": "value"\n}', encoding="utf-8")
        managed = {"extra": 1}
        mod._apply(managed, target)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["key"] == "value"
        assert result["extra"] == 1

    def test_reads_jsonc_with_block_comment_only(self, tmp_path: Path) -> None:
        """ブロックコメント (/* */) のみを含む JSONC ファイルをパースできる。"""
        target = tmp_path / "settings.json"
        target.write_text('{\n  /* ブロック\n     コメント */\n  "key": "value"\n}', encoding="utf-8")
        managed = {"extra": 2}
        mod._apply(managed, target)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["key"] == "value"
        assert result["extra"] == 2

    def test_reads_pure_json_same_result(self, tmp_path: Path) -> None:
        """純 JSON のファイルも従来どおりパースできる。"""
        target = tmp_path / "settings.json"
        target.write_text(json.dumps({"editor.fontSize": 16}, ensure_ascii=False), encoding="utf-8")
        managed = {"workbench.colorCustomizations": {"activityBar.background": "#112233"}}
        mod._apply(managed, target)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["editor.fontSize"] == 16
        assert result["workbench.colorCustomizations"]["activityBar.background"] == "#112233"

    def test_invalid_json_raises_json_decode_error(self, tmp_path: Path) -> None:
        """不正な JSON は json.JSONDecodeError を送出する。"""
        target = tmp_path / "settings.json"
        target.write_text("{invalid}", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            mod._apply({"key": "value"}, target)

    def test_reads_jsonc_with_trailing_comma(self, tmp_path: Path) -> None:
        """トレーリングカンマ付きの JSONC をパースできる。"""
        target = tmp_path / "settings.json"
        jsonc_content = '{"a": 1, "b": 2,}'
        target.write_text(jsonc_content, encoding="utf-8")
        managed = {"c": 3}
        mod._apply(managed, target)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["a"] == 1
        assert result["b"] == 2
        assert result["c"] == 3


class TestBuildManagedSettings:
    """_build_managed_settings のテスト。"""

    def test_user_scope_contains_all_keys(self) -> None:
        """User scope では markdown.styles を含むすべてのキーが含まれる。"""
        settings = mod._build_managed_settings(hostname="test", is_user_scope=True)
        assert "workbench.colorCustomizations" in settings
        assert "activityBar.background" in settings["workbench.colorCustomizations"]
        assert "markdown.styles" in settings
        assert "markdown-pdf.styles" in settings

    def test_machine_scope_excludes_markdown_styles(self) -> None:
        """Machine scope では markdown.styles を含めない (User scope に一任する)。"""
        settings = mod._build_managed_settings(hostname="test", is_user_scope=False)
        assert "markdown.styles" not in settings
        assert "workbench.colorCustomizations" in settings
        assert "markdown-pdf.styles" in settings

    def test_markdown_styles_is_jsdelivr_url(self) -> None:
        """User scope の markdown.styles は jsDelivr CDN の HTTPS URL を指す。

        GitHub raw URL に安易に差し戻されると WebView で CSS が
        拒否される (Content-Type が text/plain + nosniff のため) ので、
        jsDelivr URL であることを明示的にアサートする予防線を張る。
        """
        settings = mod._build_managed_settings(hostname="test", is_user_scope=True)
        assert settings["markdown.styles"] == [mod._MARKDOWN_STYLE_URL]
        assert mod._MARKDOWN_STYLE_URL.startswith("https://cdn.jsdelivr.net/")

    def test_markdown_pdf_styles_points_to_share_vscode(self) -> None:
        """markdown-pdf.styles は両 scope で絶対パスとして share/vscode/ の CSS を指す。"""
        for scope in (True, False):
            settings = mod._build_managed_settings(hostname="test", is_user_scope=scope)
            assert any("share/vscode/markdown-pdf.css" in p for p in settings["markdown-pdf.styles"])

    def test_css_paths_use_posix_separators(self) -> None:
        """CSS パス/URL がスラッシュ区切りである (Windows でも JSON 互換)。"""
        for scope in (True, False):
            settings = mod._build_managed_settings(hostname="test", is_user_scope=scope)
            paths = settings.get("markdown.styles", []) + settings["markdown-pdf.styles"]
            for path in paths:
                assert "\\" not in path


class TestRun:
    """run() の統合テスト。"""

    def test_skips_when_path_is_none(self) -> None:
        """settings_path が None の場合スキップする。"""
        assert mod.run(settings_path=None) is False

    def test_user_scope_writes_markdown_styles(self, tmp_path: Path) -> None:
        """Windows (User scope) では markdown.styles が書き込まれる。"""
        target = tmp_path / "settings.json"
        assert mod.run(settings_path=target, hostname="test", is_windows=True) is True
        result = json.loads(target.read_text(encoding="utf-8"))
        assert "workbench.colorCustomizations" in result
        assert result["markdown.styles"] == [mod._MARKDOWN_STYLE_URL]
        assert "markdown-pdf.styles" in result

    def test_machine_scope_skips_markdown_styles(self, tmp_path: Path) -> None:
        """Linux (Machine scope) では markdown.styles を書き込まない。"""
        target = tmp_path / "settings.json"
        assert mod.run(settings_path=target, hostname="test", is_windows=False) is True
        result = json.loads(target.read_text(encoding="utf-8"))
        assert "markdown.styles" not in result
        assert "workbench.colorCustomizations" in result
        assert "markdown-pdf.styles" in result

    def test_machine_scope_removes_legacy_markdown_styles(self, tmp_path: Path) -> None:
        """Linux (Machine scope) では既存の markdown.styles を削除する。

        過去バージョンが絶対パスを書き込んでいたケースの移行パスを保証する。
        """
        target = tmp_path / "settings.json"
        target.write_text(
            json.dumps({"markdown.styles": ["/home/aki/dotfiles/share/vscode/markdown.css"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        mod.run(settings_path=target, hostname="test", is_windows=False)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert "markdown.styles" not in result
