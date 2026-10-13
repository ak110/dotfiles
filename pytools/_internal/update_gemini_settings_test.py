"""Gemini設定の配布原本からJSONC更新・再適用までを検証する。"""

import pathlib
import sys
import typing

import pytest
import pytilpack.jsonc

from pytools._internal import common, update_gemini_settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("host_platform", ["linux", "win32"])
@pytest.mark.parametrize(
    "existing",
    [
        None,
        '{// 既存の設定\n"general":{"topicUpdateNarration":true,"previewFeatures":true},'
        '"experimental":{"topicUpdateNarration":true,"other":42},'
        '"model":{"name":"custom-model"},"security":{"auth":{"selectedType":"custom-auth"}},'
        '"mcpServers":{"local":{"command":"custom-command","args":["arg"]}},}',
        '{"experimental":{"topicUpdateNarration":true},"general":{"previewFeatures":true}}',
    ],
    ids=["absent", "enabled-jsonc", "legacy-only"],
)
def test_run_distributes_managed_settings_and_preserves_user_values(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, host_platform: str, existing: str | None
) -> None:
    """通常runから設定を作成・上書きし、ユーザー値保持と再適用を確認する。"""
    home = tmp_path / host_platform
    settings = home / ".gemini/settings.json"
    if existing is not None:
        settings.parent.mkdir(parents=True)
        settings.write_text(existing, encoding="utf-8")

    def find_root(**_kwargs: typing.Any) -> pathlib.Path:
        return REPO_ROOT

    def resolve_home(**_kwargs: typing.Any) -> pathlib.Path:
        return home

    monkeypatch.setattr(common, "find_dotfiles_root", find_root)
    monkeypatch.setattr(pathlib.Path, "home", resolve_home)
    monkeypatch.setattr(sys, "platform", host_platform)
    before = pytilpack.jsonc.loads(existing) if existing is not None else {}
    result = update_gemini_settings.run()
    assert result.changed is True
    assert result.failure is None
    assert result.next_action is None
    after = pytilpack.jsonc.loads(settings.read_text(encoding="utf-8"))
    expected = before.copy()
    expected["general"] = {**before.get("general", {}), "topicUpdateNarration": False}
    assert after == expected
    content = settings.read_bytes()
    repeated = update_gemini_settings.run()
    assert repeated.changed is False
    assert repeated.failure is None
    assert settings.read_bytes() == content


def test_run_keeps_jsonc_comments_for_value_only_change(tmp_path: pathlib.Path) -> None:
    """管理値だけの置換では既存hybrid書込がコメントも保持する。"""
    settings = tmp_path / ".gemini/settings.json"
    settings.parent.mkdir()
    settings.write_text('{\n  // ユーザーメモ\n  "general": {"topicUpdateNarration": true}\n}\n', encoding="utf-8")
    result = update_gemini_settings.run(home=tmp_path, dotfiles_root=REPO_ROOT)
    assert result.changed is True
    assert "// ユーザーメモ" in settings.read_text(encoding="utf-8")
    assert pytilpack.jsonc.loads(settings.read_text(encoding="utf-8"))["general"]["topicUpdateNarration"] is False


def test_run_reports_write_failure_with_recovery_action(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """書込失敗は失敗として返し、既存設定を保持して再実行操作を案内する。"""
    settings = tmp_path / ".gemini/settings.json"
    settings.parent.mkdir()
    original = '{"general":{"topicUpdateNarration":true}}'
    settings.write_text(original, encoding="utf-8")

    def refuse_write(*_args: typing.Any, **_kwargs: typing.Any) -> bool:
        return False

    monkeypatch.setattr(common, "write_settings_hybrid", refuse_write)
    result = update_gemini_settings.run(home=tmp_path, dotfiles_root=REPO_ROOT)
    assert result.failure
    assert result.next_action and "dotfiles-post-apply" in result.next_action
    assert result.changed is False
    assert settings.read_text(encoding="utf-8") == original


def test_run_reports_unresolved_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """原本を解決できない場合は復旧操作を持つ失敗を返す。"""

    def unresolved(**_kwargs: typing.Any) -> None:
        return None

    monkeypatch.setattr(common, "find_dotfiles_root", unresolved)
    result = update_gemini_settings.run()
    assert result.failure
    assert result.next_action and "dotfiles-post-apply" in result.next_action


@pytest.mark.parametrize("original", ["[]", "{invalid-json}"])
def test_run_rejects_invalid_settings_without_overwriting(tmp_path: pathlib.Path, original: str) -> None:
    """不正な既存入力は例外としてpost-applyへ渡し、入力ファイルを保つ。"""
    settings = tmp_path / ".gemini/settings.json"
    settings.parent.mkdir()
    settings.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError):
        update_gemini_settings.run(home=tmp_path, dotfiles_root=REPO_ROOT)
    assert settings.read_text(encoding="utf-8") == original
