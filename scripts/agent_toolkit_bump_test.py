"""scripts/agent_toolkit_bump.py の純関数テスト。"""

import json
import pathlib
import sys

import agent_toolkit_bump as bump
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agent-toolkit" / "scripts"))
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position


class TestParseVersion:
    """parse_version / format_versionの基本動作。"""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("0.40.1", (0, 40, 1)),
            ("1.0.0", (1, 0, 0)),
            ("12.34.567", (12, 34, 567)),
        ],
    )
    def test_parse_valid(self, text: str, expected: tuple[int, int, int]) -> None:
        assert bump.parse_version(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "0.40",
            "0.40.1.2",
            "0.40.x",
            "v0.40.1",
            "0.-1.0",
        ],
    )
    def test_parse_invalid(self, text: str) -> None:
        with pytest.raises(ValueError):
            bump.parse_version(text)

    def test_format_roundtrip(self) -> None:
        assert bump.format_version(bump.parse_version("0.40.1")) == "0.40.1"


class TestComputeNewVersion:
    """compute_new_version が正しい bump を行う。"""

    @pytest.mark.parametrize(
        ("current", "kind", "expected"),
        [
            ("0.40.1", "patch", "0.40.2"),
            ("0.40.1", "minor", "0.41.0"),
            ("0.40.1", "major", "1.0.0"),
            ("0.0.0", "patch", "0.0.1"),
            ("1.2.3", "minor", "1.3.0"),
            ("1.2.3", "major", "2.0.0"),
        ],
    )
    def test_bump(self, current: str, kind: bump.BumpKind, expected: str) -> None:
        assert bump.compute_new_version(current, kind) == expected


class TestInferBumpKind:
    """infer_bump_kind が base→current の差分から種別を正しく推定する。"""

    @pytest.mark.parametrize(
        ("base", "current", "expected"),
        [
            ("0.40.1", "0.40.1", None),
            ("0.40.1", "0.40.2", "patch"),
            ("0.40.1", "0.40.5", "patch"),
            ("0.40.1", "0.41.0", "minor"),
            ("0.40.1", "0.42.0", "minor"),
            ("0.40.1", "1.0.0", "major"),
            ("1.2.3", "2.0.0", "major"),
        ],
    )
    def test_valid(self, base: str, current: str, expected: bump.BumpKind | None) -> None:
        assert bump.infer_bump_kind(base, current) == expected

    @pytest.mark.parametrize(
        ("base", "current"),
        [
            # patch が残ったまま minor が上がっている: 不整合
            ("0.40.1", "0.41.1"),
            # major が上がっているが minor/patch がリセットされていない。
            ("0.40.1", "1.0.1"),
            ("0.40.1", "1.1.0"),
            # 後退（regression）。
            ("0.40.2", "0.40.1"),
            ("0.40.0", "0.39.9"),
        ],
    )
    def test_invalid(self, base: str, current: str) -> None:
        with pytest.raises(ValueError):
            bump.infer_bump_kind(base, current)


class TestBumpRanks:
    """bump 種別の順序: patch < minor < major。"""

    def test_ordering(self) -> None:
        assert bump.BUMP_RANKS["patch"] < bump.BUMP_RANKS["minor"] < bump.BUMP_RANKS["major"]


class TestWriteVersion:
    """manifest同時更新のテスト。"""

    def test_marketplace_mismatch_does_not_modify_plugin(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        """marketplace検証失敗時、plugin.jsonを部分更新しない。"""
        plugin_manifest = tmp_path / "plugin.json"
        marketplace_manifest = tmp_path / "marketplace.json"
        plugin_manifest.write_text(json.dumps({"version": "0.1.0"}) + "\n", encoding="utf-8")
        marketplace_manifest.write_text(json.dumps({"plugins": []}) + "\n", encoding="utf-8")
        monkeypatch.setattr(bump, "_PLUGIN_MANIFEST", plugin_manifest)
        monkeypatch.setattr(bump, "_MARKETPLACE_MANIFEST", marketplace_manifest)

        with pytest.raises(RuntimeError):
            bump._write_version("0.1.1")  # pylint: disable=protected-access  # noqa: SLF001

        assert json.loads(plugin_manifest.read_text(encoding="utf-8"))["version"] == "0.1.0"


class TestResolveBaseVersion:
    """基準版の解決順序と、解決できない場合の扱い。"""

    @staticmethod
    def _fake_reader(available: dict[str, str]):
        def _read(ref: str) -> str | None:
            return available.get(ref)

        return _read

    def test_prefers_upstream_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """上流ブランチを解決できる場合はそちらを採用する。"""
        monkeypatch.setattr(
            bump,
            "_read_version_at",
            self._fake_reader({"@{u}": "1.0.0", "origin/HEAD": "0.9.0"}),
        )
        assert bump.resolve_base_version() == ("1.0.0", "@{u}")

    def test_falls_back_to_origin_head(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """上流ブランチを解決できない場合はorigin/HEADを使う。"""
        monkeypatch.setattr(bump, "_read_version_at", self._fake_reader({"origin/HEAD": "0.9.0"}))
        assert bump.resolve_base_version() == ("0.9.0", "origin/HEAD")

    def test_returns_none_when_no_ref_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """いずれの参照も解決できない場合はNoneを返す。"""
        monkeypatch.setattr(bump, "_read_version_at", self._fake_reader({}))
        assert bump.resolve_base_version() is None

    def test_main_exits_nonzero_without_base_version(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """基準版を確定できない場合は増分せず非ゼロ終了する。"""
        monkeypatch.setattr(bump, "_read_current_version", lambda: "1.0.0")
        monkeypatch.setattr(bump, "resolve_base_version", lambda: None)

        def _fail(_new_version: str) -> None:
            raise AssertionError("基準版が未確定のまま書き込んではならない")

        monkeypatch.setattr(bump, "_write_version", _fail)

        assert bump.main(["patch"]) == 1
        assert "基準版を確定できなかった" in capsys.readouterr().err


class TestBumpManifestPathsSsot:
    """`_plan_format.BUMP_MANIFEST_PATHS`とのSSOT整合性検証。"""

    def test_bump_target_paths_match_plan_format_ssot(self) -> None:
        """`_PLUGIN_MANIFEST`・`_MARKETPLACE_MANIFEST`と`_plan_format.BUMP_MANIFEST_PATHS`が一致する。"""
        bump_paths = {
            bump._PLUGIN_MANIFEST.relative_to(bump._REPO_ROOT).as_posix(),  # pylint: disable=protected-access  # noqa: SLF001
            bump._MARKETPLACE_MANIFEST.relative_to(bump._REPO_ROOT).as_posix(),  # pylint: disable=protected-access  # noqa: SLF001
        }
        assert bump_paths == set(_plan_format.BUMP_MANIFEST_PATHS)
