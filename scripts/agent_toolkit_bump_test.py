"""scripts/agent_toolkit_bump.py の純関数テスト。"""

import json
import pathlib
import sys

import agent_toolkit_bump as bump
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agent-toolkit"))
# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._plan import structure as _plan_format  # noqa: E402


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


@pytest.mark.parametrize(
    ("current", "requested", "expected"),
    [
        ("0.40.1", "patch", "0.40.2"),
        ("0.40.2", "patch", None),
        ("0.40.2", "minor", "0.41.0"),
        ("0.41.0", "patch", None),
        ("0.41.0", "major", "1.0.0"),
        ("1.0.0", "minor", None),
    ],
)
def test_main_upgrades_bump_without_downgrade(
    monkeypatch: pytest.MonkeyPatch,
    current: str,
    requested: str,
    expected: str | None,
) -> None:
    """公開済み基準からbumpを格上げし、同等以下の要求では据え置く。"""
    written: list[str] = []

    def _write(_target: bump.PluginTarget, new_version: str) -> None:
        written.append(new_version)

    monkeypatch.setattr(bump, "_read_current_version", lambda _target: current)
    monkeypatch.setattr(bump, "resolve_base_version", lambda _target: ("0.40.1", "@{u}"))
    monkeypatch.setattr(bump, "_write_version", _write)

    assert bump.main([requested]) == 0
    assert written == ([] if expected is None else [expected])


def _make_repo(root: pathlib.Path) -> dict[str, pathlib.Path]:
    """2つのプラグインのmanifestとmarketplaceを持つ一時リポジトリを作り、manifestのパスを返す。"""
    manifests: dict[str, pathlib.Path] = {}
    entries = []
    for name, version in (("agent-basics", "1.6.0"), ("agent-toolkit", "2.231.0")):
        manifest = root / name / ".claude-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"name": name, "version": version}) + "\n", encoding="utf-8")
        manifests[name] = manifest
        entries.append({"name": name, "source": f"./{name}", "version": version})
    marketplace = root / ".claude-plugin" / "marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace.write_text(json.dumps({"plugins": entries}) + "\n", encoding="utf-8")
    return manifests


def _versions(root: pathlib.Path, manifests: dict[str, pathlib.Path]) -> dict[str, tuple[str, str]]:
    """プラグインごとの(plugin.jsonの版, marketplaceエントリの版)を返す。"""
    marketplace = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    listed = {entry["name"]: entry["version"] for entry in marketplace["plugins"]}
    return {name: (json.loads(path.read_text(encoding="utf-8"))["version"], listed[name]) for name, path in manifests.items()}


class TestPluginSelection:
    """`--plugin`による対象プラグインの選択。"""

    @pytest.fixture(name="repo")
    def _repo(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, pathlib.Path]:
        manifests = _make_repo(tmp_path)
        monkeypatch.setattr(bump, "_REPO_ROOT", tmp_path)
        monkeypatch.setattr(bump, "_MARKETPLACE_MANIFEST", tmp_path / ".claude-plugin" / "marketplace.json")

        def _base(target: bump.PluginTarget) -> tuple[str, str]:
            # 公開済みの基準版は作業ツリーの現在版と同じとし、未プッシュbumpが無い状態を作る。
            return json.loads(target.manifest.read_text(encoding="utf-8"))["version"], "@{u}"

        monkeypatch.setattr(bump, "resolve_base_version", _base)
        return manifests

    def test_agent_basics_updates_only_its_two_definitions(self, tmp_path: pathlib.Path, repo: dict[str, pathlib.Path]) -> None:
        """agent-basics指定はagent-basicsの2か所だけを同じ新版へ上げ、agent-toolkitの版を変えない。"""
        assert bump.main(["--plugin", "agent-basics", "minor"]) == 0
        assert _versions(tmp_path, repo) == {
            "agent-basics": ("1.7.0", "1.7.0"),
            "agent-toolkit": ("2.231.0", "2.231.0"),
        }

    def test_omitted_plugin_updates_agent_toolkit(self, tmp_path: pathlib.Path, repo: dict[str, pathlib.Path]) -> None:
        """指定を省略した既存の呼び出しはagent-toolkitを更新する。"""
        assert bump.main(["patch"]) == 0
        assert _versions(tmp_path, repo) == {
            "agent-basics": ("1.6.0", "1.6.0"),
            "agent-toolkit": ("2.231.1", "2.231.1"),
        }

    def test_unknown_plugin_is_rejected_with_accepted_names(
        self, tmp_path: pathlib.Path, repo: dict[str, pathlib.Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """marketplaceに無い名前は受理できる名前を示して非0で終了し、版数を変えない。"""
        with pytest.raises(SystemExit) as raised:
            bump.main(["--plugin", "unknown", "patch"])
        assert raised.value.code != 0
        err = capsys.readouterr().err
        assert "unknown" in err
        assert "agent-basics" in err and "agent-toolkit" in err
        assert _versions(tmp_path, repo) == {
            "agent-basics": ("1.6.0", "1.6.0"),
            "agent-toolkit": ("2.231.0", "2.231.0"),
        }


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
        monkeypatch.setattr(bump, "_MARKETPLACE_MANIFEST", marketplace_manifest)

        with pytest.raises(RuntimeError):
            bump._write_version(  # pylint: disable=protected-access  # noqa: SLF001
                bump.PluginTarget("agent-toolkit", plugin_manifest), "0.1.1"
            )

        assert json.loads(plugin_manifest.read_text(encoding="utf-8"))["version"] == "0.1.0"


class TestResolveBaseVersion:
    """基準版の解決順序と、解決できない場合の扱い。"""

    _TARGET = bump.PluginTarget("agent-toolkit", pathlib.Path("agent-toolkit/.claude-plugin/plugin.json"))

    @staticmethod
    def _fake_reader(available: dict[str, str]):
        def _read(ref: str, _target: bump.PluginTarget) -> str | None:
            return available.get(ref)

        return _read

    def test_prefers_upstream_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """上流ブランチを解決できる場合はそちらを採用する。"""
        monkeypatch.setattr(
            bump,
            "_read_version_at",
            self._fake_reader({"@{u}": "1.0.0", "origin/HEAD": "0.9.0"}),
        )
        assert bump.resolve_base_version(self._TARGET) == ("1.0.0", "@{u}")

    def test_falls_back_to_origin_head(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """上流ブランチを解決できない場合はorigin/HEADを使う。"""
        monkeypatch.setattr(bump, "_read_version_at", self._fake_reader({"origin/HEAD": "0.9.0"}))
        assert bump.resolve_base_version(self._TARGET) == ("0.9.0", "origin/HEAD")

    def test_returns_none_when_no_ref_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """いずれの参照も解決できない場合はNoneを返す。"""
        monkeypatch.setattr(bump, "_read_version_at", self._fake_reader({}))
        assert bump.resolve_base_version(self._TARGET) is None

    def test_main_exits_nonzero_without_base_version(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """基準版を確定できない場合は増分せず非ゼロ終了する。"""
        monkeypatch.setattr(bump, "_read_current_version", lambda _target: "1.0.0")
        monkeypatch.setattr(bump, "resolve_base_version", lambda _target: None)

        def _fail(_target: bump.PluginTarget, _new_version: str) -> None:
            raise AssertionError("基準版が未確定のまま書き込んではならない")

        monkeypatch.setattr(bump, "_write_version", _fail)

        assert bump.main(["patch"]) == 1
        assert "基準版を確定できなかった" in capsys.readouterr().err


class TestBumpManifestPathsSsot:
    """`_plan_format.BUMP_MANIFEST_PATHS`とのSSOT整合性検証。"""

    def test_bump_target_paths_match_plan_format_ssot(self) -> None:
        """`--plugin`省略時に更新するmanifestの集合が`_plan_format.BUMP_MANIFEST_PATHS`と一致する。"""
        target = bump.plugin_targets()[bump.DEFAULT_PLUGIN]
        bump_paths = {
            target.manifest.relative_to(bump._REPO_ROOT).as_posix(),  # pylint: disable=protected-access  # noqa: SLF001
            bump._MARKETPLACE_MANIFEST.relative_to(bump._REPO_ROOT).as_posix(),  # pylint: disable=protected-access  # noqa: SLF001
        }
        assert bump_paths == set(_plan_format.BUMP_MANIFEST_PATHS)

    def test_every_marketplace_plugin_has_its_manifest(self) -> None:
        """受理する全プラグインについて、更新するplugin.jsonが実在し、同じ名前を持つ。"""
        targets = bump.plugin_targets()
        assert {"agent-basics", "agent-toolkit"} <= set(targets)
        for name, target in targets.items():
            assert json.loads(target.manifest.read_text(encoding="utf-8"))["name"] == name
