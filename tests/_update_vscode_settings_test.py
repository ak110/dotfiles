"""_update_vscode_settings モジュールのテスト。"""

# `_hostname_color` など、パッケージ内部関数 (先頭 _) を
# テストするため protected-access を全体で許可する。
# pylint: disable=protected-access

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pytools import _update_vscode_settings as mod


class TestLoadJsonc:
    """_load_jsonc のテスト。"""

    def test_plain_json(self) -> None:
        """通常の JSON をそのままパースできる。"""
        assert mod._load_jsonc('{"key": "value"}') == {"key": "value"}

    def test_line_comments(self) -> None:
        """行コメント (//) を除去してパースできる。"""
        text = '{\n  // コメント\n  "key": "value"\n}'
        assert mod._load_jsonc(text) == {"key": "value"}

    def test_block_comments(self) -> None:
        """ブロックコメント (/* */) を除去してパースできる。"""
        text = '{\n  /* ブロック\n     コメント */\n  "key": "value"\n}'
        assert mod._load_jsonc(text) == {"key": "value"}

    def test_trailing_comma(self) -> None:
        """トレーリングカンマを除去してパースできる。"""
        text = '{\n  "a": 1,\n  "b": 2,\n}'
        assert mod._load_jsonc(text) == {"a": 1, "b": 2}

    def test_comment_like_string_preserved(self) -> None:
        """文字列内の // や /* はコメントとして扱わない。"""
        text = '{"url": "https://example.com"}'
        assert mod._load_jsonc(text) == {"url": "https://example.com"}

    def test_inline_comment_after_value(self) -> None:
        """値の後の行コメントを除去できる。"""
        text = '{\n  "key": "value" // インラインコメント\n}'
        assert mod._load_jsonc(text) == {"key": "value"}

    def test_combined_jsonc_features(self) -> None:
        """コメント・トレーリングカンマの組み合わせ。"""
        text = """{
  // 設定
  "editor.fontSize": 14,
  /* テーマ設定 */
  "workbench.colorTheme": "Default Dark+",
}"""
        result = mod._load_jsonc(text)
        assert result == {"editor.fontSize": 14, "workbench.colorTheme": "Default Dark+"}


class TestHostnameColor:
    """_hostname_color のテスト。"""

    def test_returns_valid_hex_color(self) -> None:
        """有効な 7 文字の hex カラーコードを返す。"""
        with patch.object(mod.socket, "gethostname", return_value="test-host"):
            color = mod._hostname_color()
        assert color.startswith("#")
        assert len(color) == 7
        int(color[1:], 16)  # 16 進数として有効であること

    @pytest.mark.parametrize("hostname", ["host-a", "host-b", "server-1", "x", "long-hostname-example"])
    def test_brightness_not_too_dark(self, hostname: str) -> None:
        """RGB 各チャンネルの最小値が暗すぎない (#99 = 153 以上)。"""
        with patch.object(mod.socket, "gethostname", return_value=hostname):
            color = mod._hostname_color()
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        # 加重平均輝度で検証 (0.299R + 0.587G + 0.114B)
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        assert luminance >= 140, f"色 {color} (ホスト名: {hostname}) が暗すぎる (輝度: {luminance:.0f})"

    def test_different_hostnames_produce_different_colors(self) -> None:
        """異なるホスト名は異なる色を生成する。"""
        colors = set()
        for hostname in ["host-a", "host-b", "host-c", "host-d"]:
            with patch.object(mod.socket, "gethostname", return_value=hostname):
                colors.add(mod._hostname_color())
        assert len(colors) == 4

    def test_deterministic(self) -> None:
        """同じホスト名なら同じ色を返す。"""
        with patch.object(mod.socket, "gethostname", return_value="stable-host"):
            first = mod._hostname_color()
            second = mod._hostname_color()
        assert first == second


class TestSettingsPath:
    """_settings_path のテスト。"""

    def test_linux_returns_path_when_vscode_server_exists(self, tmp_path: Path) -> None:
        """Linux: .vscode-server が存在すればパスを返す。"""
        vscode_dir = tmp_path / ".vscode-server"
        vscode_dir.mkdir()
        with patch.object(mod, "_IS_WINDOWS", False), patch.object(mod.Path, "home", return_value=tmp_path):
            path = mod._settings_path()
        assert path is not None
        assert ".vscode-server" in str(path)
        assert str(path).endswith("settings.json")

    def test_linux_returns_none_when_vscode_server_missing(self, tmp_path: Path) -> None:
        """Linux: .vscode-server が存在しなければ None を返す。"""
        with patch.object(mod, "_IS_WINDOWS", False), patch.object(mod.Path, "home", return_value=tmp_path):
            assert mod._settings_path() is None

    def test_windows_returns_path_when_code_dir_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Windows: %APPDATA%/Code が存在すればパスを返す。"""
        code_dir = tmp_path / "Code"
        code_dir.mkdir()
        monkeypatch.setenv("APPDATA", str(tmp_path))
        with patch.object(mod, "_IS_WINDOWS", True):
            path = mod._settings_path()
        assert path is not None
        assert "Code" in str(path)

    def test_windows_returns_none_when_appdata_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Windows: APPDATA 未設定なら None を返す。"""
        monkeypatch.delenv("APPDATA", raising=False)
        with patch.object(mod, "_IS_WINDOWS", True):
            assert mod._settings_path() is None


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
        target.write_text(json.dumps({"editor.fontSize": 14}), encoding="utf-8")
        managed = {"workbench.colorCustomizations": {"activityBar.background": "#aabbcc"}}
        mod._apply(managed, target)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["editor.fontSize"] == 14
        assert result["workbench.colorCustomizations"]["activityBar.background"] == "#aabbcc"

    def test_preserves_other_color_customizations(self, tmp_path: Path) -> None:
        """colorCustomizations 内の他キーが保持される (浅い dict マージ)。"""
        target = tmp_path / "settings.json"
        existing = {"workbench.colorCustomizations": {"titleBar.activeBackground": "#112233"}}
        target.write_text(json.dumps(existing), encoding="utf-8")
        managed = {"workbench.colorCustomizations": {"activityBar.background": "#aabbcc"}}
        mod._apply(managed, target)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["workbench.colorCustomizations"]["titleBar.activeBackground"] == "#112233"
        assert result["workbench.colorCustomizations"]["activityBar.background"] == "#aabbcc"

    def test_list_is_overwritten(self, tmp_path: Path) -> None:
        """list 値は上書きされる (union マージではない)。"""
        target = tmp_path / "settings.json"
        target.write_text(json.dumps({"markdown.styles": ["/old/style.css"]}), encoding="utf-8")
        managed = {"markdown.styles": ["/new/markdown.css"]}
        mod._apply(managed, target)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["markdown.styles"] == ["/new/markdown.css"]

    def test_no_change_returns_false(self, tmp_path: Path) -> None:
        """変更がなければ False を返す。"""
        target = tmp_path / "settings.json"
        managed = {"foo": "bar"}
        target.write_text(json.dumps(managed, indent=2) + "\n", encoding="utf-8")
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


class TestBuildManagedSettings:
    """_build_managed_settings のテスト。"""

    def test_contains_required_keys(self) -> None:
        """必須キーが含まれる。"""
        with patch.object(mod.socket, "gethostname", return_value="test"):
            settings = mod._build_managed_settings()
        assert "workbench.colorCustomizations" in settings
        assert "activityBar.background" in settings["workbench.colorCustomizations"]
        assert "markdown.styles" in settings
        assert "markdown-pdf.styles" in settings

    def test_css_paths_point_to_share_vscode(self) -> None:
        """CSS パスが share/vscode/ を含む。"""
        with patch.object(mod.socket, "gethostname", return_value="test"):
            settings = mod._build_managed_settings()
        assert any("share/vscode/markdown.css" in p for p in settings["markdown.styles"])
        assert any("share/vscode/markdown-pdf.css" in p for p in settings["markdown-pdf.styles"])

    def test_css_paths_use_posix_separators(self) -> None:
        """CSS パスがスラッシュ区切りである (Windows でも JSON 互換)。"""
        with patch.object(mod.socket, "gethostname", return_value="test"):
            settings = mod._build_managed_settings()
        for path in settings["markdown.styles"] + settings["markdown-pdf.styles"]:
            assert "\\" not in path


class TestRun:
    """run() の統合テスト。"""

    def test_skips_when_path_is_none(self) -> None:
        """settings_path が None の場合スキップする。"""
        with patch.object(mod, "_settings_path", return_value=None):
            assert mod.run() is False

    def test_applies_when_path_exists(self, tmp_path: Path) -> None:
        """settings_path が有効な場合マージを実行する。"""
        target = tmp_path / "settings.json"
        with (
            patch.object(mod, "_settings_path", return_value=target),
            patch.object(mod.socket, "gethostname", return_value="test"),
        ):
            assert mod.run() is True
        result = json.loads(target.read_text(encoding="utf-8"))
        assert "workbench.colorCustomizations" in result
