"""`atk config`サブコマンド（`_atk_config`モジュール）のテスト。

`atk config show`・`get`・`set`・`apply-preset`の4操作（サブコマンド省略時はshowを実行）、XDG関連パスの解決結果、
段位モデルと用途別engine順からの候補合成、旧モデル設定の移行を検証する。
"""

import json
import pathlib
from typing import cast

import pytest

from agent_toolkit import atk  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk import config as config_module  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._common import codex_models, state_paths
from agent_toolkit._common import next_action as _next_action

_LEGACY_KEYS = ("high_tier_model", "medium_tier_model", "low_tier_model", "orchestrate_model")
_SETTING_KEYS = [
    "claude_high_tier_model",
    "claude_medium_tier_model",
    "claude_low_tier_model",
    "codex_high_tier_model",
    "codex_medium_tier_model",
    "codex_low_tier_model",
    "opencode_high_tier_model",
    "opencode_medium_tier_model",
    "opencode_low_tier_model",
    "base_engine",
    "execute_engine",
    "review_engine",
    "chores_engine",
    "orchestrate_engine",
]
_PRESETS = {
    "codex-balanced": ("codex,claude", "claude,codex", "codex,claude", "codex,claude", "claude,codex"),
    "codex-near-primary": ("codex,claude", "codex,claude", "codex,claude", "codex,claude", "claude,codex"),
    "codex-primary": ("codex,claude", "codex,claude", "codex,claude", "codex,claude", "codex,claude"),
    "claude-balanced": ("claude,codex", "claude,codex", "codex,claude", "codex,claude", "claude,codex"),
    "claude-primary": ("claude,codex", "claude,codex", "claude,codex", "claude,codex", "claude,codex"),
    "opencode-balanced": (
        "opencode,codex,claude",
        "claude,codex",
        "opencode,codex,claude",
        "opencode,codex,claude",
        "claude,codex",
    ),
    "opencode-near-primary": (
        "opencode,codex,claude",
        "opencode,codex,claude",
        "opencode,codex,claude",
        "opencode,codex,claude",
        "claude,codex",
    ),
}
_ENGINE_KEYS = ("base_engine", "execute_engine", "review_engine", "chores_engine", "orchestrate_engine")


@pytest.mark.parametrize("tier", config_module.TIERS)
def test_opencode_empty_model_and_provider_model_effort(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tier: str
) -> None:
    """未設定段位を外し、provider/modelとeffortを分離し、空の環境上書きでも無効にする。"""
    key = f"opencode_{tier}_tier_model"
    assert _main(tmp_path, "config", "get", key) == 0
    assert capsys.readouterr().out == "\n"
    assert _main(tmp_path, "config", "set", "review_engine", "opencode,claude") == 0
    assert config_module.parse_unresolved_model_candidates(f"{tier}_tier", usage="review")[0][0] == "claude"
    for value, effort in (("local/qwen", "medium"), ("local/qwen@high", "high")):
        assert _main(tmp_path, "config", "set", key, value) == 0
        assert config_module.parse_unresolved_model_candidates(f"{tier}_tier", usage="review")[0] == (
            "opencode",
            "local/qwen",
            effort,
        )
        assert config_module.parse_unresolved_model_candidates(f"opencode:{value}") == [("opencode", "local/qwen", effort)]
    monkeypatch.setenv(f"AGENT_TOOLKIT_CONFIG_{key.upper()}", "")
    assert config_module.parse_unresolved_model_candidates(f"{tier}_tier", usage="review")[0][0] == "claude"
    assert _main(tmp_path, "config", "set", key, "") == 0


def test_opencode_orchestrator_rejected_from_set_and_environment(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """設定CLIと環境変数のどちらもorchestrator用opencodeを拒否する。"""
    assert _main(tmp_path, "config", "set", "orchestrate_engine", "opencode,claude") == 2
    assert "受理可能書式" in capsys.readouterr().err
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_ORCHESTRATE_ENGINE", "opencode,claude")
    assert _main(tmp_path, "config", "get", "orchestrate_engine") == 2
    assert "受理可能書式" in capsys.readouterr().err


def test_opencode_does_not_expand_write_model_scope(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """段位の追加はwrite_modelの既存のengine集合を変えず、保存と環境指定で同じ区分を使う。"""
    value = "opencode:local/Qwen/Qwen3.8-27B@thinking"
    assert _main(tmp_path, "config", "set", "write_model", value) == 2
    assert "<claude|codex|agy>" in capsys.readouterr().err
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WRITE_MODEL", value)
    assert _main(tmp_path, "config", "get", "write_model") == 2
    assert "<claude|codex|agy>" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("value", "model", "effort"),
    [
        ("local/Qwen/Qwen3.8-27B", "local/Qwen/Qwen3.8-27B", "medium"),
        ("local/Qwen/Qwen3.8-27B@thinking", "local/Qwen/Qwen3.8-27B", "thinking"),
        ("local/Qwen/Qwen3.8-27B/high", "local/Qwen/Qwen3.8-27B/high", "medium"),
        ("local/org/medium", "local/org/medium", "medium"),
        ("local/org/medium@low", "local/org/medium", "low"),
    ],
)
def test_opencode_keeps_model_path_separate_from_variant(tmp_path: pathlib.Path, value: str, model: str, effort: str) -> None:
    """名前空間と推論量に一致する末尾を含むモデルIDを保ち、@で明示したvariantを分離する。"""
    assert _main(tmp_path, "config", "set", "opencode_high_tier_model", value) == 0
    assert _main(tmp_path, "config", "set", "base_engine", "opencode") == 0
    assert config_module.parse_unresolved_model_candidates("high_tier") == [("opencode", model, effort)]
    assert config_module.parse_unresolved_model_candidates(f"opencode:{value}") == [("opencode", model, effort)]


def test_opencode_preset_warns_after_saving_and_empty_only_order_fails(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """プリセットを保存してから未設定キーを案内し、候補ゼロの原因も示す。"""
    assert _main(tmp_path, "config", "apply-preset", "opencode-balanced") == 0
    result = capsys.readouterr()
    for tier in config_module.TIERS:
        assert f"opencode_{tier}_tier_model" in result.err
    assert "atk config set" in result.err
    assert _saved(tmp_path)["base_engine"] == "opencode,codex,claude"
    assert _main(tmp_path, "config", "set", "base_engine", "opencode") == 0
    with pytest.raises(ValueError, match="モデル候補がない"):
        config_module.parse_unresolved_model_candidates("high_tier")


@pytest.mark.parametrize("preset", [None, *[name for name in _PRESETS if not name.startswith("opencode-")]])
def test_existing_presets_never_select_configured_opencode(tmp_path: pathlib.Path, preset: str | None) -> None:
    """初期設定と既存5プリセットはopencodeの全段位が設定済みでも選択しない。"""
    for tier in config_module.TIERS:
        assert _main(tmp_path, "config", "set", f"opencode_{tier}_tier_model", "local/qwen/high") == 0
    if preset is not None:
        assert _main(tmp_path, "config", "apply-preset", preset) == 0
    for usage in config_module.USAGES:
        for tier in config_module.TIERS:
            assert all(
                engine != "opencode"
                for engine, _, _ in config_module.parse_unresolved_model_candidates(f"{tier}_tier", usage=usage)
            )


def test_claude_api_mode_accepts_two_values_and_env_override(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """show・get・setと環境上書きで接続方針の保存と不正値拒否を観測する。"""
    assert _main(tmp_path, "config", "show") == 0
    assert "claude_api_mode pay-as-you-go" in capsys.readouterr().out
    for mode in ("api-credits", "pay-as-you-go"):
        assert _main(tmp_path, "config", "set", "claude_api_mode", mode) == 0
        capsys.readouterr()
        assert _main(tmp_path, "config", "get", "claude_api_mode") == 0
        assert capsys.readouterr().out.strip() == mode
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_MODE", "api-credits")
    assert _main(tmp_path, "config", "get", "claude_api_mode") == 0
    assert capsys.readouterr().out.strip() == "api-credits"
    assert _main(tmp_path, "config", "set", "claude_api_mode", "invalid") == 2
    error = capsys.readouterr().err
    assert "pay-as-you-go" in error and "api-credits" in error
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_MODE", "invalid")
    assert _main(tmp_path, "config", "get", "claude_api_mode") == 2


def test_claude_api_env_file_accepts_missing_path(tmp_path: pathlib.Path) -> None:
    """将来作成するファイルのパスを設定CLIから保存できる。"""
    path = str(tmp_path / "future.env")
    assert _main(tmp_path, "config", "set", "claude_api_env_file", path) == 0
    assert config_module.resolve_mutable_setting("claude_api_env_file") == path


@pytest.fixture(autouse=True)
def _isolate_platformdirs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`platformdirs`の解決先を実環境から隔離する。"""
    monkeypatch.setattr(config_module.platformdirs, "user_config_dir", lambda _name, **_kwargs: str(tmp_path / "config"))
    monkeypatch.setattr(config_module.platformdirs, "user_state_dir", lambda _name, **_kwargs: str(tmp_path / "state"))
    monkeypatch.setattr(config_module.platformdirs, "user_data_dir", lambda _name, **_kwargs: str(tmp_path / "data"))
    for key in (*config_module._MUTABLE_KEY_DEFAULTS, *_LEGACY_KEYS):  # pylint: disable=protected-access  # noqa: SLF001
        monkeypatch.delenv(f"AGENT_TOOLKIT_CONFIG_{key.upper()}", raising=False)
    monkeypatch.setattr(
        codex_models,
        "list_models",
        lambda: [
            {"model": "gpt-6-sol", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]},
            {
                "model": "gpt-6-luna",
                "supportedReasoningEfforts": [{"reasoningEffort": effort} for effort in ("medium", "xhigh")],
            },
            {"model": "gpt-5.6-terra", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]},
        ],
    )


def _main(tmp_path: pathlib.Path, *arguments: str) -> int:
    """`atk`を実行し、終了コードを返す。"""
    with pytest.raises(SystemExit) as exc_info:
        atk.main(list(arguments), home=tmp_path)
    return cast(int, exc_info.value.code)


def _config_file(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "config" / "config.json"


def _write_config(tmp_path: pathlib.Path, values: dict[str, str]) -> pathlib.Path:
    """隔離した設定保存先へ保存値を書き、そのパスを返す。"""
    path = _config_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values) + "\n", encoding="utf-8")
    return path


def _saved(tmp_path: pathlib.Path) -> dict[str, str]:
    return json.loads(_config_file(tmp_path).read_text(encoding="utf-8"))


def test_show_line_can_be_copied_to_set(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """showのキーを加工せずsetへ渡し、コロンを含む値をgetできる。"""
    value = "claude:opus/high,codex:sol/medium"
    assert _main(tmp_path, "config", "set", "write_model", value) == 0
    capsys.readouterr()
    for argv in (("config", "show"), ("config",)):
        assert _main(tmp_path, *argv) == 0
        lines = capsys.readouterr().out.splitlines()
        assert all(" " in line and not line.partition(" ")[0].endswith(":") for line in lines)
        key, copied = next(line for line in lines if line.startswith("write_model ")).split(" ", 1)
        assert _main(tmp_path, "config", "set", key, copied) == 0
        capsys.readouterr()
        assert _main(tmp_path, "config", "get", key) == 0
        assert capsys.readouterr().out.rstrip("\n") == value


class TestConfigShow:
    """`atk config`（サブコマンド省略時はshow扱い）・`atk config show`の一覧表示を検証する。"""

    def test_show_lists_settings_in_table_order(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """パスの後に段位モデル6キー、用途別engine5キーを設定表の順に並べ、他のキーも保持する。"""
        assert _main(tmp_path, "config", "show") == 0

        out = capsys.readouterr().out
        assert f"config_dir {tmp_path / 'config'}" in out
        assert f"state_dir {tmp_path / 'state'}" in out
        assert f"data_dir {tmp_path / 'data'}" in out
        keys = [line.partition(" ")[0] for line in out.splitlines()]
        assert keys == [
            "config_dir",
            "state_dir",
            "data_dir",
            "private_notes",
            *_SETTING_KEYS,
            "write_model",
            "codex_fast_mode",
            "codex_model_providers",
            "claude_api_env_file",
            "claude_api_mode",
            "process_loop_repositories",
        ]
        for key in config_module._MUTABLE_KEY_DEFAULTS:  # pylint: disable=protected-access  # noqa: SLF001
            assert f"{key} {config_module.resolve_mutable_setting(key)}" in out
        for legacy in _LEGACY_KEYS:
            assert f"\n{legacy}:" not in f"\n{out}"

    def test_key_guidance_uses_table_order(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """setとgetのキー案内も設定表の順に並べる。"""
        assert _main(tmp_path, "config", "set", "no-such-key", "x") == 2
        set_error = capsys.readouterr().err
        assert _main(tmp_path, "config", "get", "no-such-key") == 2
        get_error = capsys.readouterr().err
        for error in (set_error, get_error):
            positions = [error.index(key) for key in [*_SETTING_KEYS, "write_model"]]
            assert positions == sorted(positions)

    def test_no_subcommand_defaults_to_show(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """`atk config`（サブコマンド省略）は`show`と同じ出力になる。"""
        assert _main(tmp_path, "config", "show") == 0
        shown = capsys.readouterr().out
        assert _main(tmp_path, "config") == 0
        assert capsys.readouterr().out == shown

    def test_show_keeps_candidate_outside_known_list_without_warning(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """参考一覧外のモデルとeffortも現在の値として表示する。"""
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_HIGH_TIER_MODEL", "unknown-model/ultra")
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_HIGH_TIER_MODEL", "another-unknown/medium")

        assert _main(tmp_path, "config", "show") == 0
        captured = capsys.readouterr()
        assert "codex_high_tier_model unknown-model/ultra" in captured.out
        assert not captured.err

    def test_show_emits_no_warning_when_all_candidates_known(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """全候補が参考一覧内なら標準エラーへ何も書かない。"""
        assert _main(tmp_path, "config", "show") == 0
        captured = capsys.readouterr()
        assert "claude_high_tier_model " in captured.out
        assert not captured.err

    def test_show_silently_ignores_removed_model_keys(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """保存済みの旧工程キーは表示にも警告にも現れない。"""
        removed = (
            "plan_model",
            "plan_review_model",
            "execute_fast_model",
            "explore_model",
            "explore_fast_model",
            "pick_wi_model",
            "execute_model",
            "execute_review_model",
        )
        _write_config(tmp_path, dict.fromkeys(removed, "codex:obsolete/low"))

        assert _main(tmp_path, "config", "show") == 0
        captured = capsys.readouterr()
        for key in removed:
            assert f"{key}:" not in captured.out
        assert not captured.err


@pytest.mark.parametrize(("value", "expected"), [("", ""), ("  ", ""), (" a, b,a ", "a,b")])
def test_codex_provider_config_cli(tmp_path, capsys, monkeypatch, value, expected):
    """provider列を公開CLIで正規化・保存・取得・解除し、モデルの警告を適用しない。"""
    key = "codex_model_providers"
    with pytest.raises(SystemExit, match="0"):
        atk.main(["config", "set", key, value], home=tmp_path)
    assert not capsys.readouterr().err
    assert json.loads((tmp_path / "config" / "config.json").read_text(encoding="utf-8"))[key] == expected
    with pytest.raises(SystemExit, match="0"):
        atk.main(["config", "get", key], home=tmp_path)
    assert capsys.readouterr().out == expected + "\n"
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_MODEL_PROVIDERS", " b, c,b ")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["config", "get", key], home=tmp_path)
    assert capsys.readouterr().out == "b,c\n"
    monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_CODEX_MODEL_PROVIDERS")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["config", "set", key, ""], home=tmp_path)
    capsys.readouterr()
    with pytest.raises(SystemExit, match="0"):
        atk.main(["config", "get", key], home=tmp_path)
    assert capsys.readouterr().out == "\n"


@pytest.mark.parametrize("value", [",a", "a,", "a,,b", "a, ,b"])
def test_codex_provider_empty_element_rejected(tmp_path, capsys, monkeypatch, value):
    """保存値と環境変数の空候補を拒否し、設定ファイルの不在を保つ。"""
    key = "codex_model_providers"
    with pytest.raises(SystemExit, match="2"):
        atk.main(["config", "set", key, value], home=tmp_path)
    assert not (tmp_path / "config" / "config.json").exists()
    capsys.readouterr()
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_MODEL_PROVIDERS", value)
    with pytest.raises(SystemExit, match="2"):
        atk.main(["config", "get", key], home=tmp_path)
    assert not capsys.readouterr().out


def test_codex_provider_setting_clear_preserves_other_settings(tmp_path, capsys):
    """公開操作で接続先列を解除し、独立した速度設定を保持する。"""
    path = tmp_path / "config" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"codex_model_providers": "paid-a", "codex_fast_mode": "true"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["config", "get", "codex_model_providers"], home=tmp_path)
    initial = capsys.readouterr()
    assert initial.out == "paid-a\n"
    assert not initial.err
    with pytest.raises(SystemExit, match="0"):
        atk.main(["config", "set", "codex_model_providers", ""], home=tmp_path)
    assert not capsys.readouterr().err
    assert json.loads(path.read_text(encoding="utf-8")) == {"codex_model_providers": "", "codex_fast_mode": "true"}
    with pytest.raises(SystemExit, match="0"):
        atk.main(["config", "show"], home=tmp_path)
    assert not capsys.readouterr().err


def test_claude_api_env_config_cli(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """存在しないパスを保存・表示でき、環境上書きと解除を解釈する。"""
    for arguments, expected in [
        (["set", "claude_api_env_file", "~/missing.env"], ""),
        (["get", "claude_api_env_file"], str(pathlib.Path.home() / "missing.env")),
        (["show"], "claude_api_env_file"),
        (["set", "claude_api_env_file", ""], ""),
        (["get", "claude_api_env_file"], ""),
    ]:
        assert _main(tmp_path, "config", *arguments) == 0
        output = capsys.readouterr()
        if expected:
            assert expected in output.out
        elif arguments[0] == "get":
            assert output.out == "\n"
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", "~/override.env")
    assert config_module.resolve_mutable_setting("claude_api_env_file") == str(pathlib.Path.home() / "override.env")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", "")
    assert config_module.resolve_mutable_setting("claude_api_env_file") == ""


class TestCodexFastMode:
    """公開config操作で速度の実効値・保存値・環境変数と既存プリセットの独立性を確かめる。"""

    @pytest.mark.parametrize("value", ["true", "false"])
    def test_public_round_trip_and_preset_preservation(
        self, value: str, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "get", "codex_fast_mode"], home=tmp_path)
        assert capsys.readouterr().out == "false\n"
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "set", "codex_fast_mode", value], home=tmp_path)
        assert not capsys.readouterr().err
        path = tmp_path / "config" / "config.json"
        assert json.loads(path.read_text(encoding="utf-8"))["codex_fast_mode"] == value
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "apply-preset", "codex-primary"], home=tmp_path)
        capsys.readouterr()
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "get", "codex_fast_mode"], home=tmp_path)
        assert capsys.readouterr().out == f"{value}\n"
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "show"], home=tmp_path)
        shown = capsys.readouterr()
        assert f"codex_fast_mode {value}\n" in shown.out
        assert not shown.err

    @pytest.mark.parametrize("value", ["TRUE", "False", "1", "0", "yes", "", "codex:gpt-6-sol/medium"])
    def test_rejects_other_values_without_writing(
        self, value: str, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "config" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"codex_fast_mode": "true"}\n', encoding="utf-8")
        original = path.read_bytes()
        with pytest.raises(SystemExit, match="2"):
            atk.main(["config", "set", "codex_fast_mode", value], home=tmp_path)
        assert "true, false" in capsys.readouterr().err
        assert path.read_bytes() == original

    def test_environment_overrides_saved_value_and_warns_on_set(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_FAST_MODE", "true")
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "set", "codex_fast_mode", "false"], home=tmp_path)
        assert "AGENT_TOOLKIT_CONFIG_CODEX_FAST_MODEが優先" in capsys.readouterr().err
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "get", "codex_fast_mode"], home=tmp_path)
        assert capsys.readouterr().out == "true\n"
        monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_CODEX_FAST_MODE")
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "get", "codex_fast_mode"], home=tmp_path)
        assert capsys.readouterr().out == "false\n"


class TestConfigGet:
    """`atk config get`の1件以上のキー取得を検証する。"""

    def test_get_known_key(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """既知キーの値のみを1行で出力する。"""
        assert _main(tmp_path, "config", "get", "config_dir") == 0
        assert capsys.readouterr().out == f"{tmp_path / 'config'}\n"

    def test_get_state_dir_matches_public_helper(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """`state_dir()`の戻り値と`atk config get state_dir`の出力が一致する。

        フックは`state_dir()`経由で状態ファイルを配置する。
        両者が一致しない場合、ユーザーが`atk config get`で確認した位置と実際の配置先が異なる。
        """
        assert _main(tmp_path, "config", "get", "state_dir") == 0
        assert capsys.readouterr().out == f"{state_paths.state_dir()}\n"

    def test_get_state_dir_rejects_relative_xdg_state_home(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """相対XDG_STATE_HOMEはHOME配下へ退避し、状態読取側と同じ絶対パスを返す。"""
        monkeypatch.setenv("XDG_STATE_HOME", "relative-state")
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setattr(
            config_module.platformdirs,
            "user_state_dir",
            lambda _name, **_kwargs: "relative-state/agent-toolkit",
        )

        assert _main(tmp_path, "config", "get", "state_dir") == 0
        expected = tmp_path / "home" / ".local" / "state" / "agent-toolkit"
        assert capsys.readouterr().out == f"{expected}\n"

    def test_get_defaults_follow_settings_table(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """未設定のモデルとengine順は指定した入力順で返し、設定を保存しない。"""
        requested = list(reversed(_SETTING_KEYS))
        assert _main(tmp_path, "config", "get", *requested) == 0
        expected = {
            "claude_high_tier_model": "opus[1m]/medium",
            "claude_medium_tier_model": "sonnet[1m]/medium",
            "claude_low_tier_model": "haiku/medium",
            "codex_high_tier_model": "sol/medium",
            "codex_medium_tier_model": "terra/medium",
            "codex_low_tier_model": "luna/medium",
            "opencode_high_tier_model": "",
            "opencode_medium_tier_model": "",
            "opencode_low_tier_model": "",
            "base_engine": "codex,claude",
            "execute_engine": "claude,codex",
            "review_engine": "codex,claude",
            "chores_engine": "codex,claude",
            "orchestrate_engine": "claude,codex",
        }
        assert capsys.readouterr().out.splitlines() == [expected[key] for key in requested]
        assert not _config_file(tmp_path).exists()

    def test_family_setting_tracks_new_model_without_rewriting_saved_value(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """系列名を保存したまま新世代の利用可能モデルへ解決する。"""
        monkeypatch.setattr(
            codex_models,
            "list_models",
            lambda: [
                {"model": "gpt-6-sol", "supportedReasoningEfforts": [{"reasoningEffort": "high"}]},
                {
                    "model": "gpt-6.1-sol",
                    "supportedReasoningEfforts": [{"reasoningEffort": effort} for effort in ("medium", "high")],
                },
            ],
        )
        assert _main(tmp_path, "config", "set", "codex_high_tier_model", "sol/high") == 0
        assert _main(tmp_path, "config", "set", "base_engine", "codex") == 0
        capsys.readouterr()

        assert _main(tmp_path, "config", "get", "codex_high_tier_model") == 0
        assert capsys.readouterr().out == "sol/high\n"
        assert config_module.resolve_model_candidates("high_tier") == [("codex", "gpt-6.1-sol", "high")]
        assert _saved(tmp_path)["codex_high_tier_model"] == "sol/high"

    def test_explicit_codex_id_does_not_require_model_catalog(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """完全IDは一覧が取得できない条件でも固定指定として返す。"""
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_HIGH_TIER_MODEL", "gpt-5.6-sol/medium")
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_BASE_ENGINE", "codex")

        def fail_catalog() -> list[dict[str, object]]:
            raise AssertionError("完全IDの解決でmodel/listを呼ばない")

        monkeypatch.setattr(codex_models, "list_models", fail_catalog)
        assert _main(tmp_path, "config", "get", "codex_high_tier_model") == 0
        assert capsys.readouterr().out == "gpt-5.6-sol/medium\n"
        assert config_module.resolve_model_candidates("high_tier") == [("codex", "gpt-5.6-sol", "medium")]

    @pytest.mark.parametrize(
        ("model", "message"),
        [("sol/max", "reasoning effort max"), ("astra/medium", "astra系列")],
    )
    def test_family_resolution_failure_is_deferred_to_start(
        self,
        monkeypatch: pytest.MonkeyPatch,
        model: str,
        message: str,
    ) -> None:
        """設定表示は値を保持し、利用できない系列の失敗は起動時の解決へ渡す。"""
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_HIGH_TIER_MODEL", model)
        assert config_module.resolve_mutable_setting("codex_high_tier_model") == model
        with pytest.raises(ValueError, match=message):
            config_module.resolve_model_candidates("high_tier")

    def test_get_multiple_keys_in_requested_order(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """複数キーの値を指定順に1行ずつ出力する。"""
        assert _main(tmp_path, "config", "get", "data_dir", "orchestrate_engine", "config_dir") == 0
        assert capsys.readouterr().out == f"{tmp_path / 'data'}\nclaude,codex\n{tmp_path / 'config'}\n"

    def test_get_unknown_key_exits_2(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """未知キーはexit 2でエラー案内を出力する。"""
        assert _main(tmp_path, "config", "get", "no-such-key") == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert "claude_high_tier_model" in captured.err
        assert "execute_fix_model" not in captured.err

    def test_get_known_and_unknown_keys_is_atomic(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """既知キーの間に未知キーがある場合は値を出力せずexit 2とする。"""
        assert _main(tmp_path, "config", "get", "config_dir", "no-such-key", "data_dir") == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert "base_engine" in captured.err

    @pytest.mark.parametrize("legacy", _LEGACY_KEYS)
    def test_legacy_key_get_is_rejected_with_replacements(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], legacy: str
    ) -> None:
        """旧モデル設定キーはgetの対象外とし、置き換える新キーを示す。"""
        assert _main(tmp_path, "config", "get", legacy) == 2
        error = capsys.readouterr().err
        assert f"未知の設定キーを指定した: {legacy}" in error
        tier = "high" if legacy == "orchestrate_model" else legacy.split("_")[0]
        assert f"claude_{tier}_tier_model" in error.split("旧モデル設定キーは廃止した", 1)[1]

    def test_environment_override_applies_to_get_and_show_then_restores_saved_value(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """空でない新しい環境変数は保存値より優先されて実効値になり、保存値へ書き込まれない。"""
        assert _main(tmp_path, "config", "set", "claude_high_tier_model", "opus/high") == 0
        capsys.readouterr()

        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_HIGH_TIER_MODEL", "sonnet/low")
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_BASE_ENGINE", "claude")
        assert _main(tmp_path, "config", "get", "claude_high_tier_model", "base_engine") == 0
        assert capsys.readouterr().out == "sonnet/low\nclaude\n"
        assert _main(tmp_path, "config", "show") == 0
        assert "claude_high_tier_model sonnet/low" in capsys.readouterr().out
        assert config_module.resolve_model_candidates("high_tier") == [("claude", "sonnet", "low")]
        assert _saved(tmp_path) == {"claude_high_tier_model": "opus/high"}

        monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_CLAUDE_HIGH_TIER_MODEL")
        assert _main(tmp_path, "config", "get", "claude_high_tier_model") == 0
        assert capsys.readouterr().out == "opus/high\n"

    @pytest.mark.parametrize("subcommand", [["get", "codex_high_tier_model"], ["show"]])
    def test_invalid_environment_override_exits_2(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        subcommand: list[str],
    ) -> None:
        """不正な環境変数値は実効値へ渡さず、変数名と値を示して拒否する。"""
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_HIGH_TIER_MODEL", "sol/medium,terra/medium")

        assert _main(tmp_path, "config", *subcommand) == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert "AGENT_TOOLKIT_CONFIG_CODEX_HIGH_TIER_MODEL" in captured.err
        assert "sol/medium,terra/medium" in captured.err
        next_action = next(line for line in captured.err.splitlines() if line.startswith("次の操作: "))
        assert "AGENT_TOOLKIT_CONFIG_CODEX_HIGH_TIER_MODEL" in next_action

    def test_empty_environment_override_is_ignored(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """環境変数が空文字列なら未指定として扱い、設定の初期値を返す。"""
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REVIEW_ENGINE", "")
        assert _main(tmp_path, "config", "get", "review_engine") == 0
        assert capsys.readouterr().out == "codex,claude\n"

    def test_immutable_environment_name_does_not_override_private_notes(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """変更可能キー以外を模した環境変数は既存の設定値の解決に影響しない。"""
        assert _main(tmp_path, "config", "get", "private_notes") == 0
        original = capsys.readouterr().out

        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PRIVATE_NOTES", "/unexpected")

        assert _main(tmp_path, "config", "get", "private_notes") == 0
        assert capsys.readouterr().out == original


class TestConfigApplyPreset:
    """`atk config apply-preset`のプリセットと入力検証を検証する。"""

    @pytest.mark.parametrize("preset", list(_PRESETS))
    def test_apply_preset_saves_only_engine_settings(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        preset: str,
    ) -> None:
        """プリセット表のengine設定5件だけを保存し、段位モデル・write_model・他の保存値を保持する。"""
        kept = {
            "other_setting": "keep",
            "write_model": "claude:claude-opus-5-5/medium",
            "codex_high_tier_model": "gpt-6-sol/high",
            "claude_low_tier_model": "sonnet/low",
            **{f"opencode_{tier}_tier_model": "local/qwen/low" for tier in config_module.TIERS},
        }
        _write_config(tmp_path, kept)
        expected = dict(zip(_ENGINE_KEYS, _PRESETS[preset], strict=True))

        assert _main(tmp_path, "config", "apply-preset", preset) == 0
        captured = capsys.readouterr()
        assert not captured.err
        assert captured.out.splitlines() == [
            f"成功: 用途別engine設定をpreset「{preset}」で一括保存した: 5件",
            *(f"{key}: {value}" for key, value in expected.items()),
        ]
        assert _saved(tmp_path) == {**kept, **expected}

    def test_apply_preset_rejects_unknown_name(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """未知名は`show`と候補5件を表示してexit 2とする。"""
        assert _main(tmp_path, "config", "apply-preset", "unknown-preset") == 2
        captured = capsys.readouterr()
        assert not captured.out
        for preset in ("show", *_PRESETS):
            assert preset in captured.err

    @pytest.mark.parametrize("existing_config", [False, True])
    @pytest.mark.parametrize("arguments", [["show"], []])
    def test_apply_preset_show_and_omitted_print_all_presets_without_saving(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        arguments: list[str],
        existing_config: bool,
    ) -> None:
        """`show`と引数の省略は5プリセットのengine設定だけを保存せずに表示する。"""
        original = json.dumps({"claude_low_tier_model": "sonnet/high"}) + "\n"
        if existing_config:
            _config_file(tmp_path).parent.mkdir(parents=True)
            _config_file(tmp_path).write_text(original, encoding="utf-8")
        expected_lines: list[str] = []
        for preset, values in _PRESETS.items():
            expected_lines.append(f"{preset}:")
            expected_lines.extend(f"  {key}: {value}" for key, value in zip(_ENGINE_KEYS, values, strict=True))

        assert _main(tmp_path, "config", "apply-preset", *arguments) == 0
        captured = capsys.readouterr()
        assert not captured.err
        assert captured.out.splitlines() == expected_lines
        if existing_config:
            assert _config_file(tmp_path).read_text(encoding="utf-8") == original
        else:
            assert not _config_file(tmp_path).exists()


class TestConfigSet:
    """`atk config set`の変更可能設定更新を検証する。"""

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("codex_high_tier_model", "gpt-6-sol/medium"),
            ("codex_low_tier_model", "gpt-6-luna/xhigh"),
            ("codex_medium_tier_model", "terra"),
            ("claude_medium_tier_model", "sonnet"),
            ("claude_high_tier_model", "opus[1m]/high"),
            ("claude_low_tier_model", "fable/medium"),
            ("base_engine", "claude"),
            ("review_engine", "codex,claude"),
            ("write_model", "codex:gpt-6-sol/medium,claude:opus/high"),
        ],
    )
    def test_set_persists_and_is_read_back(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], key: str, value: str
    ) -> None:
        """受理形式の値を永続化し、以降の`get`へ反映する。"""
        assert _main(tmp_path, "config", "set", key, value) == 0
        captured = capsys.readouterr()
        assert captured.out == f"成功: 設定を更新した: {key}={value}\n"
        assert captured.err == ""

        assert _main(tmp_path, "config", "get", key) == 0
        assert capsys.readouterr().out == f"{value}\n"
        expected = {key: value}
        if key == "base_engine":
            expected["execute_engine"] = value
        assert _saved(tmp_path) == expected

    @pytest.mark.parametrize(
        "value",
        ["", "codex:sol/medium", "claude:opus", "sol/medium,terra/medium", "sol/medium ", " sol", "sol/", "a/b/c", "sol,"],
    )
    def test_set_rejects_invalid_tier_model(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], value: str
    ) -> None:
        """engine接頭辞、空要素、空白、複数モデルを拒否し、保存しない。"""
        assert _main(tmp_path, "config", "set", "codex_high_tier_model", value) == 2
        assert "<model>[/<effort>]" in capsys.readouterr().err
        assert not _config_file(tmp_path).exists()

    @pytest.mark.parametrize("value", ["", "codex,codex", "agy", "codex, claude", "codex,", "Codex", "claude,codex,claude"])
    def test_set_rejects_invalid_engine_order(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], value: str
    ) -> None:
        """codexとclaude以外、重複、空要素、空白を含むengine順を拒否する。"""
        assert _main(tmp_path, "config", "set", "chores_engine", value) == 2
        assert "codex・claude・opencodeを重複なし" in capsys.readouterr().err
        assert not _config_file(tmp_path).exists()

    @pytest.mark.parametrize(
        "key",
        ("explore_model", "explore_fast_model", "pick_wi_model", "execute_model", "execute_review_model", "execute_fix_model"),
    )
    def test_removed_stage_keys_are_rejected(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], key: str
    ) -> None:
        """旧工程キーのsetを拒否し、変更可能なキーを案内する。"""
        assert _main(tmp_path, "config", "set", key, "codex:sol/medium") == 2
        error = capsys.readouterr().err
        assert f"変更できない設定キーを指定した: {key}" in error
        for new_key in (*_SETTING_KEYS, "write_model"):
            assert new_key in error
        assert not _config_file(tmp_path).exists()

    @pytest.mark.parametrize("legacy", _LEGACY_KEYS)
    def test_legacy_model_keys_are_rejected_with_replacements(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], legacy: str
    ) -> None:
        """旧モデル設定キーはsetの対象外とし、置き換える新キーを示す。"""
        assert _main(tmp_path, "config", "set", legacy, "codex:sol/medium") == 2
        error = capsys.readouterr().err
        assert f"変更できない設定キーを指定した: {legacy}" in error
        engine_key = {
            "high_tier_model": "base_engine",
            "medium_tier_model": "review_engine",
            "low_tier_model": "chores_engine",
            "orchestrate_model": "orchestrate_engine",
        }[legacy]
        assert f"{legacy} → {engine_key}" in error
        assert not _config_file(tmp_path).exists()

    def test_legacy_execute_fix_model_get_is_rejected(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """旧`execute_fix_model`は`get`でも未知キーとして拒否する。"""
        assert _main(tmp_path, "config", "get", "execute_fix_model") == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert "失敗: 未知の設定キーを指定した: execute_fix_model" in captured.err

    def test_set_preserves_unconfigured_defaults(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """設定保存では、未設定のキーへ初期値を保存しない。"""
        _write_config(tmp_path, {"other_setting": "keep", "session_review_model": "claude:opus/medium"})

        assert _main(tmp_path, "config", "set", "codex_high_tier_model", "gpt-5.6-terra/medium") == 0
        assert capsys.readouterr().err == ""
        assert _saved(tmp_path) == {
            "other_setting": "keep",
            "session_review_model": "claude:opus/medium",
            "codex_high_tier_model": "gpt-5.6-terra/medium",
        }

    def test_set_with_environment_override_warns_but_updates_saved_value(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """環境変数が優先中でも保存先を更新し、実効値にならないことを警告する。"""
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_BASE_ENGINE", "claude")

        assert _main(tmp_path, "config", "set", "base_engine", "codex") == 0
        captured = capsys.readouterr()
        next_action = next(line for line in captured.err.splitlines() if line.startswith("次の操作: "))
        assert "AGENT_TOOLKIT_CONFIG_BASE_ENGINE" in next_action
        assert _saved(tmp_path)["base_engine"] == "codex"

    def test_set_unknown_model_and_effort_warns_and_persists(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """参考一覧に無いモデル名・effortは警告を表示したうえで受理し、永続化する。"""
        assert _main(tmp_path, "config", "set", "codex_medium_tier_model", "new-model/ultra") == 0
        captured = capsys.readouterr()
        assert "成功: 設定を更新した: codex_medium_tier_model=new-model/ultra" in captured.out
        assert "モデル名`new-model`は主に使うモデルの一覧" in captured.err
        assert "effort`ultra`は主に使う値の一覧" in captured.err
        assert "意図した名前か確認し、誤記なら同じキーへ" in captured.err
        assert captured.err.count("警告: ") == 2
        assert captured.err.count("次の操作: ") == 1
        assert _saved(tmp_path) == {"codex_medium_tier_model": "new-model/ultra"}

    def test_write_model_candidate_warnings_identify_only_unknown_candidate(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """write_modelは候補ごとに既知の一覧との一致を確かめ、一覧外の候補だけを警告する。"""
        known = "codex:gpt-6-astra/low"
        unknown = "claude:unknown-model/medium"

        assert _main(tmp_path, "config", "set", "write_model", f"{known},{unknown}") == 0
        captured = capsys.readouterr()
        assert f"候補`{unknown}`" in captured.err
        assert f"候補`{known}`" not in captured.err

    @pytest.mark.parametrize("value", ["gpt-5.6-sol", "other:model", "codex:", "claude:model/", "codex:a/b, claude:b"])
    def test_set_invalid_write_model_exits_2(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], value: str
    ) -> None:
        """write_modelの不正な候補列はexit 2で受理可能書式を案内する。"""
        assert _main(tmp_path, "config", "set", "write_model", value) == 2
        assert "<claude|codex|agy>:<model>[/<effort>]" in capsys.readouterr().err

    @pytest.mark.parametrize("key", ["merge_model", "codex_model", "config_dir"])
    def test_set_unknown_or_immutable_key_exits_2(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], key: str
    ) -> None:
        """廃止したキーと導出値キーは変更できずexit 2でエラー案内を出力する。"""
        assert _main(tmp_path, "config", "set", key, "codex:gpt-5.6-sol/medium") == 2
        assert f"失敗: 変更できない設定キーを指定した: {key}" in capsys.readouterr().err


class TestCandidateResolution:
    """段位モデル設定と用途別engine順から候補を合成する処理を検証する。"""

    def test_parse_candidates_normalizes_effort_and_preserves_distinct_values(self) -> None:
        """候補列を3つ組へ分解し、省略effortを補完して異なるeffortを保持する。"""
        assert config_module.parse_stage_model_candidates("codex:gpt-5.6-sol,claude:sonnet/high,claude:sonnet/low") == [
            ("codex", "gpt-5.6-sol", "medium"),
            ("claude", "sonnet", "high"),
            ("claude", "sonnet", "low"),
        ]

    @pytest.mark.parametrize(
        ("model_type", "usage", "expected"),
        [
            ("high_tier", None, [("codex", "gpt-6-sol", "medium"), ("claude", "opus[1m]", "medium")]),
            ("medium_tier", None, [("codex", "gpt-5.6-terra", "medium"), ("claude", "sonnet[1m]", "medium")]),
            ("low_tier", None, [("codex", "gpt-6-luna", "medium"), ("claude", "haiku", "medium")]),
            ("medium_tier", "base", [("codex", "gpt-5.6-terra", "medium"), ("claude", "sonnet[1m]", "medium")]),
            ("high_tier", "chores", [("codex", "gpt-6-sol", "medium"), ("claude", "opus[1m]", "medium")]),
            ("high_tier", "review", [("codex", "gpt-6-sol", "medium"), ("claude", "opus[1m]", "medium")]),
            ("high_tier", "execute", [("claude", "opus[1m]", "medium"), ("codex", "gpt-6-sol", "medium")]),
            ("orchestrate", None, [("claude", "opus[1m]", "medium"), ("codex", "gpt-6-sol", "medium")]),
            ("orchestrate", "chores", [("claude", "opus[1m]", "medium"), ("codex", "gpt-6-sol", "medium")]),
            ("write", "base", [("agy", "gemini-3.8-flash", "medium"), ("claude", "claude-opus-5-5", "medium")]),
        ],
    )
    def test_default_candidates_by_usage_and_tier(
        self, model_type: str, usage: str | None, expected: list[tuple[str, str, str]]
    ) -> None:
        """段位名は用途のengine順と段位モデルから合成し、orchestrate・writeは用途を参照しない。"""
        assert config_module.resolve_model_candidates(model_type, usage=usage) == expected

    def test_changing_one_tier_model_keeps_other_models_and_engine_orders(self, tmp_path: pathlib.Path) -> None:
        """段位モデル1個の変更はそのengine・段位の候補だけを変え、用途別engine順を保持する。"""
        before = {
            (model_type, usage): config_module.parse_unresolved_model_candidates(model_type, usage=usage)
            for model_type in ("high_tier", "medium_tier", "low_tier", "orchestrate")
            for usage in ("base", "review", "chores")
        }
        assert _main(tmp_path, "config", "set", "codex_high_tier_model", "sol-next/high") == 0

        for (model_type, usage), candidates in before.items():
            after = config_module.parse_unresolved_model_candidates(model_type, usage=usage)
            assert [engine for engine, _model, _effort in after] == [engine for engine, _model, _effort in candidates]
            expected = [
                ("codex", "sol-next", "high") if engine == "codex" and model_type in {"high_tier", "orchestrate"} else item
                for item in candidates
                for engine in (item[0],)
            ]
            assert after == expected

    @pytest.mark.parametrize("preset", ["codex-balanced", "claude-balanced"])
    def test_balanced_review_uses_other_engine_first(self, tmp_path: pathlib.Path, preset: str) -> None:
        """balancedではレビュー用途の先頭engineがexecuteと逆になり、小さい作業はchoresのengine順でCodexを先頭にする。"""
        assert _main(tmp_path, "config", "apply-preset", preset) == 0
        execute = config_module.parse_unresolved_model_candidates("high_tier", usage="execute")
        review = config_module.parse_unresolved_model_candidates("medium_tier", usage="review")
        chores = config_module.parse_unresolved_model_candidates("low_tier", usage="chores")
        assert review[0][0] != execute[0][0]
        assert chores[0][0] == "codex"

    def test_unknown_model_type_and_usage_are_rejected(self) -> None:
        """未知のmodel_typeは受理形式を示し、未知の用途は用途の一覧を示して拒否する。"""
        with pytest.raises(ValueError, match=r"unknown model_type: no-such.*high_tier.*medium_tier"):
            config_module.resolve_model_candidates("no-such")
        for old_type in ("execute", "execute_review", "explore", "explore_fast", "pick_wi"):
            with pytest.raises(ValueError, match=f"unknown model_type: {old_type}"):
                config_module.resolve_model_candidates(old_type)
        with pytest.raises(ValueError, match=r"or pass candidates like codex:gpt-6-sol/medium"):
            config_module.resolve_model_candidates("no-such")
        with pytest.raises(ValueError, match=r"unknown usage: write"):
            config_module.resolve_model_candidates("high_tier", usage="write")

    def test_direct_candidates_do_not_read_settings(self, tmp_path: pathlib.Path) -> None:
        """設定値と同じ書式の候補列を直接受理し、設定と旧保存値を読まずにその候補を返す。"""
        path = _write_config(tmp_path, {"high_tier_model": "agy:gemini-3.8-flash/medium", "base_engine": "claude"})
        original = path.read_bytes()

        assert config_module.resolve_model_candidates("codex:gpt-5.6-sol/medium", usage="base") == [
            ("codex", "gpt-5.6-sol", "medium")
        ]
        assert config_module.resolve_model_candidates("codex:a/low,claude:opus/high") == [
            ("codex", "a", "low"),
            ("claude", "opus", "high"),
        ]
        assert path.read_bytes() == original

    @pytest.mark.parametrize("value", ["codex:", "nosuch:model", "codex:a/b/c"])
    def test_malformed_direct_candidates_are_rejected(self, value: str) -> None:
        """候補列としても種別としても成立しない値は`ValueError`で拒否する。"""
        with pytest.raises(ValueError, match=r"unknown model_type"):
            config_module.resolve_model_candidates(value)


class TestLegacyMigration:
    """旧モデル設定（4キー）の保存値の移行と、表現できない場合の停止を検証する。"""

    def test_get_migrates_legacy_values_once_and_keeps_engine_orders(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """旧保存値だけの環境でgetを行うと、モデル値と候補順を新キーへ一括移行し、再度の操作で繰り返さない。"""
        path = _write_config(
            tmp_path,
            {
                "high_tier_model": "codex:sol/medium,claude:opus[1m]/medium",
                "medium_tier_model": "codex:terra/medium,claude:sonnet[1m]/medium",
                "low_tier_model": "codex:luna/medium,claude:haiku/medium",
                "orchestrate_model": "claude:opus[1m]/medium,codex:sol/medium",
                "write_model": "claude:opus/high",
                "other_setting": "keep",
            },
        )

        assert _main(tmp_path, "config", "get", "review_engine") == 0
        assert capsys.readouterr().out == "codex,claude\n"
        migrated = _saved(tmp_path)
        assert migrated == {
            "write_model": "claude:opus/high",
            "other_setting": "keep",
            "base_engine": "codex,claude",
            "execute_engine": "codex,claude",
            "review_engine": "codex,claude",
            "chores_engine": "codex,claude",
            "orchestrate_engine": "claude,codex",
            "codex_high_tier_model": "sol/medium",
            "claude_high_tier_model": "opus[1m]/medium",
            "codex_medium_tier_model": "terra/medium",
            "claude_medium_tier_model": "sonnet[1m]/medium",
            "codex_low_tier_model": "luna/medium",
            "claude_low_tier_model": "haiku/medium",
        }
        snapshot = path.read_bytes()
        assert _main(tmp_path, "config", "show") == 0
        capsys.readouterr()
        assert path.read_bytes() == snapshot

    def test_candidate_resolution_migrates_and_unsaved_legacy_keys_use_new_defaults(self, tmp_path: pathlib.Path) -> None:
        """設定からの候補解決でも移行し、保存されていない旧キーと省略されたengineは新しい未設定時の値を使う。"""
        _write_config(tmp_path, {"low_tier_model": "claude:sonnet/low"})

        assert config_module.parse_unresolved_model_candidates("low_tier") == [("claude", "sonnet", "low")]
        assert _saved(tmp_path) == {"chores_engine": "claude", "claude_low_tier_model": "sonnet/low"}
        assert config_module.parse_unresolved_model_candidates("high_tier") == [
            ("codex", "sol", "medium"),
            ("claude", "opus[1m]", "medium"),
        ]

    def test_saved_new_keys_take_precedence_over_legacy_values(self, tmp_path: pathlib.Path) -> None:
        """新キーが保存済みならその値を保ち、旧保存値で上書きしない。"""
        _write_config(
            tmp_path,
            {
                "high_tier_model": "claude:opus/high,codex:sol/high",
                "base_engine": "codex",
                "claude_high_tier_model": "fable/medium",
            },
        )

        assert config_module.parse_unresolved_model_candidates("high_tier") == [("codex", "sol", "high")]
        assert _saved(tmp_path) == {
            "base_engine": "codex",
            "execute_engine": "codex",
            "claude_high_tier_model": "fable/medium",
            "codex_high_tier_model": "sol/high",
        }

    def test_environment_values_are_not_migrated_or_saved(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """移行は保存値だけを対象とし、新しい環境変数の値を保存しない。"""
        _write_config(tmp_path, {"medium_tier_model": "codex:terra/high"})
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REVIEW_ENGINE", "claude")

        assert config_module.parse_unresolved_model_candidates("medium_tier") == [("claude", "sonnet[1m]", "medium")]
        assert _saved(tmp_path) == {"review_engine": "codex", "codex_medium_tier_model": "terra/high"}

    @pytest.mark.parametrize(
        ("saved", "legacy", "candidate", "fixes"),
        [
            (
                {"high_tier_model": "agy:gemini-3.8-flash/medium,claude:opus/high"},
                "high_tier_model",
                "`agy:gemini-3.8-flash/medium`",
                {"base_engine": "claude"},
            ),
            (
                {"orchestrate_model": "claude:opus/high,claude:sonnet/medium"},
                "orchestrate_model",
                "`claude:opus/high`, `claude:sonnet/medium`",
                {"orchestrate_engine": "claude", "claude_high_tier_model": "opus/high"},
            ),
            (
                {"high_tier_model": "codex:sol/medium", "orchestrate_model": "codex:sol/high"},
                "orchestrate_model",
                "`sol/high`",
                {"codex_high_tier_model": "sol/high"},
            ),
        ],
    )
    def test_unrepresentable_legacy_values_stop_until_new_keys_are_saved(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        saved: dict[str, str],
        legacy: str,
        candidate: str,
        fixes: dict[str, str],
    ) -> None:
        """表現できない旧値は競合と修正する新キーを全件示して停止し、旧保存値を保ち、新キーの明示保存で復旧する。"""
        path = _write_config(tmp_path, saved)
        original = path.read_bytes()

        assert _main(tmp_path, "config", "show") == 2
        error = capsys.readouterr().err
        assert legacy in error
        assert candidate in error
        for key in fixes:
            assert f"修正する新キー: {key}" in error
        assert "旧保存値は変更していません" in error
        assert path.read_bytes() == original
        with pytest.raises(_next_action.ActionableError, match=next(iter(fixes))):
            config_module.parse_unresolved_model_candidates("orchestrate")
        assert path.read_bytes() == original

        for key, value in fixes.items():
            assert _main(tmp_path, "config", "set", key, value) == 0
        capsys.readouterr()
        assert _main(tmp_path, "config", "get", *fixes) == 0
        assert capsys.readouterr().out.splitlines() == list(fixes.values())
        migrated = _saved(tmp_path)
        assert not set(_LEGACY_KEYS) & set(migrated)
        assert {key: migrated[key] for key in fixes} == fixes

    def test_apply_preset_after_migration_resets_only_engine_orders(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """移行したカスタム候補順は次のプリセット適用まで保持され、適用後もモデル値は保持される。"""
        _write_config(tmp_path, {"low_tier_model": "claude:sonnet/low,codex:luna/high"})
        assert _main(tmp_path, "config", "get", "chores_engine") == 0
        assert capsys.readouterr().out == "claude,codex\n"

        assert _main(tmp_path, "config", "apply-preset", "codex-balanced") == 0
        capsys.readouterr()
        assert _main(tmp_path, "config", "get", "chores_engine", "claude_low_tier_model", "codex_low_tier_model") == 0
        assert capsys.readouterr().out == "codex,claude\nsonnet/low\nluna/high\n"

    def test_legacy_environment_variable_stops_launch_with_replacement(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """旧環境変数を残した設定からの起動は、置き換える新しい環境変数を案内して止まる。"""
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_ORCHESTRATE_MODEL", "codex:sol/medium")

        with pytest.raises(_next_action.ActionableError) as exc_info:
            config_module.parse_unresolved_model_candidates("orchestrate")
        assert "AGENT_TOOLKIT_CONFIG_ORCHESTRATE_MODEL" in str(exc_info.value)
        assert "AGENT_TOOLKIT_CONFIG_ORCHESTRATE_ENGINE" in exc_info.value.next_action
        assert "AGENT_TOOLKIT_CONFIG_CODEX_HIGH_TIER_MODEL" in exc_info.value.next_action
        assert config_module.parse_unresolved_model_candidates("write")
        assert config_module.parse_unresolved_model_candidates("codex:gpt-6-sol/medium")
        assert not _config_file(tmp_path).exists()


@pytest.mark.parametrize("explicit_execute", [None, "claude"])
@pytest.mark.parametrize("environment", [None, "codex,claude"])
def test_execute_inherits_saved_base_once_and_preserves_explicit_configuration(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    explicit_execute: str | None,
    environment: str | None,
) -> None:
    """実行用途の旧保存値引継ぎ、明示値・環境値の優先と、後続base変更からの独立性を公開操作で確かめる。"""
    kept = {
        "claude_high_tier_model": "opus/high",
        "claude_medium_tier_model": "sonnet/high",
        "claude_low_tier_model": "haiku/low",
        "codex_high_tier_model": "sol/high",
        "codex_medium_tier_model": "terra/high",
        "codex_low_tier_model": "luna/low",
        "write_model": "claude:opus/medium",
        "codex_model_providers": "paid-a",
        "review_engine": "claude,codex",
    }
    original = {**kept, "base_engine": "codex"}
    if explicit_execute is not None:
        original["execute_engine"] = explicit_execute
    path = _write_config(tmp_path, original)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_BASE_ENGINE", "claude")
    if environment is not None:
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_EXECUTE_ENGINE", environment)
    assert _main(tmp_path, "config", "get", "execute_engine") == 0
    inherited = explicit_execute or "codex"
    assert capsys.readouterr().out == (environment or inherited) + "\n"
    assert _saved(tmp_path) == {**original, "execute_engine": inherited}
    snapshot = path.read_bytes()
    assert _main(tmp_path, "config", "get", "execute_engine") == 0
    capsys.readouterr()
    assert path.read_bytes() == snapshot
    monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_BASE_ENGINE")
    assert _main(tmp_path, "config", "set", "base_engine", "claude") == 0
    capsys.readouterr()
    monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_EXECUTE_ENGINE", raising=False)
    assert _main(tmp_path, "config", "get", "execute_engine") == 0
    assert capsys.readouterr().out == inherited + "\n"
    assert _saved(tmp_path) == {**kept, "base_engine": "claude", "execute_engine": inherited}
