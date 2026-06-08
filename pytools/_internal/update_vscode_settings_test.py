"""update_vscode_settings モジュールのテスト。"""

import itertools
import json
import math
from pathlib import Path

import pytest

from pytools._internal import update_vscode_settings as mod

_MARKDOWN_STYLE_URL = mod._MARKDOWN_STYLE_URL  # noqa: SLF001  # pylint: disable=protected-access  # 本番URLとのSSOT保持のため直接参照


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _make_settings_dir(tmp_path: Path, *, is_windows: bool) -> Path:
    """OS 別の VSCode settings.json パスを tmp_path 配下に作成して返す。"""
    if is_windows:
        code_dir = tmp_path / "Code"
        code_dir.mkdir(exist_ok=True)
        return code_dir / "User" / "settings.json"
    vscode_server = tmp_path / ".vscode-server"
    vscode_server.mkdir(exist_ok=True)
    return vscode_server / "data" / "Machine" / "settings.json"


class TestHostnameColor:
    """`run()` 経由でホスト名カラーの動作を検証する。"""

    def test_returns_valid_hex_color(self, tmp_path: Path) -> None:
        """有効な 7 文字の hex カラーコードが activityBar.background に書き込まれる。"""
        target = _make_settings_dir(tmp_path, is_windows=False)
        mod.run(hostname="test-host", is_windows=False, home=tmp_path)
        result = json.loads(target.read_text(encoding="utf-8"))
        color = result["workbench.colorCustomizations"]["activityBar.background"]
        assert color.startswith("#")
        assert len(color) == 7
        int(color[1:], 16)  # 16 進数として有効であること

    def test_palette_brightness_not_too_dark(self) -> None:
        """パレット全色の加重平均輝度が閾値 140 以上である。

        run() では1色しか生成しないため、パレット定数を直接参照して全色を検証する。
        """
        for color in mod._HOST_COLORS:  # noqa: SLF001  # pylint: disable=protected-access  # パレット全色の品質検証はrun()経由で到達不能（1ホスト=1色のみ返るため）。coding-standardsの例外条件（引数注入では到達不能なロジックに限り最小限の範囲で許容）に該当
            r, g, b = _hex_to_rgb(color)
            luminance = 0.299 * r + 0.587 * g + 0.114 * b
            assert luminance >= 140, f"パレット色 {color} が暗すぎる (輝度: {luminance:.1f})"

    def test_color_is_in_palette(self, tmp_path: Path) -> None:
        """複数ホスト名の戻り値が常にパレット内に含まれる。"""
        palette = set(mod._HOST_COLORS)  # noqa: SLF001  # pylint: disable=protected-access  # パレット全色の品質検証はrun()経由で到達不能（1ホスト=1色のみ返るため）。coding-standardsの例外条件（引数注入では到達不能なロジックに限り最小限の範囲で許容）に該当
        target = _make_settings_dir(tmp_path, is_windows=False)
        for hostname in ["host-a", "host-b", "server-1", "x", "long-hostname-example", "desk", "laptop"]:
            if target.exists():
                target.unlink()
            mod.run(hostname=hostname, is_windows=False, home=tmp_path)
            result = json.loads(target.read_text(encoding="utf-8"))
            color = result["workbench.colorCustomizations"]["activityBar.background"]
            assert color in palette

    def test_palette_min_distance(self) -> None:
        """パレット内の全ペアの RGB ユークリッド距離が十分に離れている。

        連続 HSL サンプリング時代にユーザーが「区別がつかない」と報告した近接ペアの
        距離は約 23.96 だった。将来パレットを差し替えた際にも、少なくとも閾値 40 を
        下回らないことをパレット自体のサニティーテストとして担保する。
        """
        min_distance = min(
            math.dist(_hex_to_rgb(a), _hex_to_rgb(b))
            for a, b in itertools.combinations(mod._HOST_COLORS, 2)  # noqa: SLF001  # pylint: disable=protected-access  # パレット全色の品質検証はrun()経由で到達不能（1ホスト=1色のみ返るため）。coding-standardsの例外条件（引数注入では到達不能なロジックに限り最小限の範囲で許容）に該当
        )
        assert min_distance >= 40.0, f"パレット内の最小ペア距離 {min_distance:.2f} が閾値 40 を下回っている"

    def test_deterministic(self, tmp_path: Path) -> None:
        """同じホスト名なら同じ色を返す。"""
        target = _make_settings_dir(tmp_path, is_windows=False)
        mod.run(hostname="stable-host", is_windows=False, home=tmp_path)
        first = json.loads(target.read_text(encoding="utf-8"))["workbench.colorCustomizations"]["activityBar.background"]
        # ファイルを削除してから再度 run() する
        target.unlink()
        mod.run(hostname="stable-host", is_windows=False, home=tmp_path)
        second = json.loads(target.read_text(encoding="utf-8"))["workbench.colorCustomizations"]["activityBar.background"]
        assert first == second


class TestSettingsPath:
    """`run()` 経由で settings.json パス解決の動作を検証する。"""

    def test_linux_returns_path_when_vscode_server_exists(self, tmp_path: Path) -> None:
        """Linux: .vscode-server が存在すればファイルが作成される。"""
        (tmp_path / ".vscode-server").mkdir()
        assert mod.run(is_windows=False, home=tmp_path) is True
        assert (tmp_path / ".vscode-server" / "data" / "Machine" / "settings.json").exists()

    def test_linux_returns_none_when_vscode_server_missing(self, tmp_path: Path) -> None:
        """Linux: .vscode-server が存在しなければ False を返す（ファイル未作成）。"""
        assert mod.run(is_windows=False, home=tmp_path) is False

    def test_windows_returns_path_when_code_dir_exists(self, tmp_path: Path) -> None:
        """Windows: %APPDATA%/Code が存在すればファイルが作成される。"""
        (tmp_path / "Code").mkdir()
        assert mod.run(is_windows=True, environ={"APPDATA": str(tmp_path)}) is True
        assert (tmp_path / "Code" / "User" / "settings.json").exists()

    def test_windows_returns_none_when_appdata_missing(self) -> None:
        """Windows: APPDATA 未設定なら False を返す。"""
        assert mod.run(is_windows=True, environ={}) is False


class TestApply:
    """`run()` 経由で managed 設定のマージ動作を検証する。"""

    def test_creates_new_file(self, tmp_path: Path) -> None:
        """settings.json が存在しない場合、managed がそのまま出力される。"""
        target = _make_settings_dir(tmp_path, is_windows=False)
        assert mod.run(hostname="test", is_windows=False, home=tmp_path) is True
        result = json.loads(target.read_text(encoding="utf-8"))
        assert "workbench.colorCustomizations" in result
        assert "activityBar.background" in result["workbench.colorCustomizations"]

    def test_preserves_existing_keys(self, tmp_path: Path) -> None:
        """既存キーが保持される。"""
        target = _make_settings_dir(tmp_path, is_windows=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"editor.fontSize": 14}, ensure_ascii=False), encoding="utf-8")
        mod.run(hostname="test", is_windows=False, home=tmp_path)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["editor.fontSize"] == 14
        assert "workbench.colorCustomizations" in result

    def test_preserves_other_color_customizations(self, tmp_path: Path) -> None:
        """colorCustomizations 内の他キーが保持される (浅い dict マージ)。"""
        target = _make_settings_dir(tmp_path, is_windows=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = {"workbench.colorCustomizations": {"titleBar.activeBackground": "#112233"}}
        target.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
        mod.run(hostname="test", is_windows=False, home=tmp_path)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["workbench.colorCustomizations"]["titleBar.activeBackground"] == "#112233"
        assert "activityBar.background" in result["workbench.colorCustomizations"]

    def test_list_is_overwritten(self, tmp_path: Path) -> None:
        """list 値は上書きされる (union マージではない)。"""
        target = _make_settings_dir(tmp_path, is_windows=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"markdown-pdf.styles": ["/old/markdown-pdf.css"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        (tmp_path / "Code").mkdir(exist_ok=True)
        mod.run(hostname="test", is_windows=True, environ={"APPDATA": str(tmp_path)})
        result = json.loads(target.read_text(encoding="utf-8"))
        # managed の markdown-pdf.styles で上書きされる
        assert "/old/markdown-pdf.css" not in result["markdown-pdf.styles"]

    def test_legacy_keys_are_removed(self, tmp_path: Path) -> None:
        """Machine scope では markdown.styles が削除される。"""
        target = _make_settings_dir(tmp_path, is_windows=False)
        target.parent.mkdir(parents=True, exist_ok=True)
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
        mod.run(hostname="test", is_windows=False, home=tmp_path)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert "markdown.styles" not in result
        assert result["editor.fontSize"] == 14

    def test_no_change_returns_false(self, tmp_path: Path) -> None:
        """変更がなければ False を返す。"""
        _make_settings_dir(tmp_path, is_windows=False)
        # 先に run() して現在の managed 設定を書き込む
        mod.run(hostname="test", is_windows=False, home=tmp_path)
        # 同じ設定で再度 run() すれば変更なし
        assert mod.run(hostname="test", is_windows=False, home=tmp_path) is False

    def test_reads_jsonc_with_comments(self, tmp_path: Path) -> None:
        """JSONC (コメント付き) ファイルをパースしてマージできる。"""
        target = _make_settings_dir(tmp_path, is_windows=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        jsonc_content = """{
  // エディター設定
  "editor.fontSize": 14,
  /* テーマ */
  "workbench.colorTheme": "Default Dark+",
}"""
        target.write_text(jsonc_content, encoding="utf-8")
        mod.run(hostname="test", is_windows=False, home=tmp_path)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["editor.fontSize"] == 14
        assert result["workbench.colorTheme"] == "Default Dark+"
        assert "workbench.colorCustomizations" in result

    def test_reads_jsonc_with_line_comment_only(self, tmp_path: Path) -> None:
        """行コメント (//) のみを含む JSONC ファイルをパースできる。"""
        target = _make_settings_dir(tmp_path, is_windows=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{\n  // 行コメント\n  "key": "value"\n}', encoding="utf-8")
        mod.run(hostname="test", is_windows=False, home=tmp_path)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["key"] == "value"

    def test_reads_jsonc_with_block_comment_only(self, tmp_path: Path) -> None:
        """ブロックコメント (/* */) のみを含む JSONC ファイルをパースできる。"""
        target = _make_settings_dir(tmp_path, is_windows=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{\n  /* ブロック\n     コメント */\n  "key": "value"\n}', encoding="utf-8")
        mod.run(hostname="test", is_windows=False, home=tmp_path)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["key"] == "value"

    def test_reads_pure_json_same_result(self, tmp_path: Path) -> None:
        """純 JSON のファイルも従来どおりパースできる。"""
        target = _make_settings_dir(tmp_path, is_windows=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"editor.fontSize": 16}, ensure_ascii=False), encoding="utf-8")
        mod.run(hostname="test", is_windows=False, home=tmp_path)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["editor.fontSize"] == 16

    def test_invalid_json_raises_json_decode_error(self, tmp_path: Path) -> None:
        """不正な JSON は json.JSONDecodeError を送出する。"""
        target = _make_settings_dir(tmp_path, is_windows=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{invalid}", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            mod.run(hostname="test", is_windows=False, home=tmp_path)

    def test_reads_jsonc_with_trailing_comma(self, tmp_path: Path) -> None:
        """トレーリングカンマ付きの JSONC をパースできる。"""
        target = _make_settings_dir(tmp_path, is_windows=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"a": 1, "b": 2,}', encoding="utf-8")
        mod.run(hostname="test", is_windows=False, home=tmp_path)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["a"] == 1
        assert result["b"] == 2


class TestBuildManagedSettings:
    """`run()` 経由で managed 設定のビルド結果を検証する。"""

    def test_user_scope_contains_all_keys(self, tmp_path: Path) -> None:
        """User scope では markdown.styles を含むすべてのキーが含まれる。"""
        target = _make_settings_dir(tmp_path, is_windows=True)
        (tmp_path / "Code").mkdir(exist_ok=True)
        mod.run(hostname="test", is_windows=True, environ={"APPDATA": str(tmp_path)})
        result = json.loads(target.read_text(encoding="utf-8"))
        assert "workbench.colorCustomizations" in result
        assert "activityBar.background" in result["workbench.colorCustomizations"]
        assert "markdown.styles" in result
        assert "markdown-pdf.styles" in result

    def test_machine_scope_excludes_markdown_styles(self, tmp_path: Path) -> None:
        """Machine scope では markdown.styles を含めない (User scope に一任する)。"""
        target = _make_settings_dir(tmp_path, is_windows=False)
        mod.run(hostname="test", is_windows=False, home=tmp_path)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert "markdown.styles" not in result
        assert "workbench.colorCustomizations" in result
        assert "markdown-pdf.styles" in result

    def test_markdown_styles_is_jsdelivr_url(self, tmp_path: Path) -> None:
        """User scope の markdown.styles は jsDelivr CDN の HTTPS URL を指す。

        GitHub raw URL に安易に差し戻されると WebView で CSS が
        拒否される (Content-Type が text/plain + nosniff のため) ので、
        jsDelivr URL であることを明示的にアサートする予防線を設ける。
        """
        target = _make_settings_dir(tmp_path, is_windows=True)
        (tmp_path / "Code").mkdir(exist_ok=True)
        mod.run(hostname="test", is_windows=True, environ={"APPDATA": str(tmp_path)})
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result["markdown.styles"] == [_MARKDOWN_STYLE_URL]
        assert _MARKDOWN_STYLE_URL.startswith("https://cdn.jsdelivr.net/")

    def test_markdown_pdf_styles_points_to_share_vscode(self, tmp_path: Path) -> None:
        """markdown-pdf.styles は両 scope で絶対パスとして share/vscode/ の CSS を指す。"""
        # User scope (Windows)
        target = _make_settings_dir(tmp_path, is_windows=True)
        if target.exists():
            target.unlink()
        mod.run(hostname="test", is_windows=True, environ={"APPDATA": str(tmp_path)})
        result = json.loads(target.read_text(encoding="utf-8"))
        assert any("share/vscode/markdown-pdf.css" in p for p in result["markdown-pdf.styles"])

        # Machine scope (Linux)
        target = _make_settings_dir(tmp_path, is_windows=False)
        if target.exists():
            target.unlink()
        mod.run(hostname="test", is_windows=False, home=tmp_path)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert any("share/vscode/markdown-pdf.css" in p for p in result["markdown-pdf.styles"])

    def test_css_paths_use_posix_separators(self, tmp_path: Path) -> None:
        """CSS パス/URL がスラッシュ区切りである (Windows でも JSON 互換)。"""
        # User scope (Windows)
        target = _make_settings_dir(tmp_path, is_windows=True)
        if target.exists():
            target.unlink()
        mod.run(hostname="test", is_windows=True, environ={"APPDATA": str(tmp_path)})
        result = json.loads(target.read_text(encoding="utf-8"))
        for path in result.get("markdown.styles", []) + result["markdown-pdf.styles"]:
            assert "\\" not in path

        # Machine scope (Linux)
        target = _make_settings_dir(tmp_path, is_windows=False)
        if target.exists():
            target.unlink()
        mod.run(hostname="test", is_windows=False, home=tmp_path)
        result = json.loads(target.read_text(encoding="utf-8"))
        for path in result.get("markdown.styles", []) + result["markdown-pdf.styles"]:
            assert "\\" not in path


class TestRun:
    """run() の統合テスト。"""

    def test_skips_when_settings_path_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`_settings_path` が None を返す場合スキップする。"""
        monkeypatch.setattr(mod, "_settings_path", lambda **_kwargs: None)
        assert mod.run() is False

    def test_user_scope_writes_markdown_styles(self, tmp_path: Path) -> None:
        """Windows (User scope) では markdown.styles が書き込まれる。"""
        target = _make_settings_dir(tmp_path, is_windows=True)
        (tmp_path / "Code").mkdir(exist_ok=True)
        assert mod.run(hostname="test", is_windows=True, environ={"APPDATA": str(tmp_path)}) is True
        result = json.loads(target.read_text(encoding="utf-8"))
        assert "workbench.colorCustomizations" in result
        assert result["markdown.styles"] == [_MARKDOWN_STYLE_URL]
        assert "markdown-pdf.styles" in result

    def test_machine_scope_skips_markdown_styles(self, tmp_path: Path) -> None:
        """Linux (Machine scope) では markdown.styles を書き込まない。"""
        target = _make_settings_dir(tmp_path, is_windows=False)
        assert mod.run(hostname="test", is_windows=False, home=tmp_path) is True
        result = json.loads(target.read_text(encoding="utf-8"))
        assert "markdown.styles" not in result
        assert "workbench.colorCustomizations" in result
        assert "markdown-pdf.styles" in result

    def test_machine_scope_removes_legacy_markdown_styles(self, tmp_path: Path) -> None:
        """Linux (Machine scope) では既存の markdown.styles を削除する。

        過去バージョンが絶対パスを書き込んでいたケースの移行パスを保証する。
        """
        target = _make_settings_dir(tmp_path, is_windows=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"markdown.styles": ["/home/aki/dotfiles/share/vscode/markdown.css"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        mod.run(hostname="test", is_windows=False, home=tmp_path)
        result = json.loads(target.read_text(encoding="utf-8"))
        assert "markdown.styles" not in result
