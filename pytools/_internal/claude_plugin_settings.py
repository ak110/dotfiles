"""Claude Code plugin の user scope の有効設定を読み取り、導入前の明示的な無効化を復元する。"""

import json
from typing import cast

from pytools._internal import claude_common, common


class PluginSettingsError(RuntimeError):
    """plugin の設定を保持・検証できなかった理由と復旧の操作。"""

    def __init__(self, reason: str, next_action: str) -> None:
        super().__init__(reason)
        self.next_action = next_action


def disable_plugin(plugin_id: str) -> str | None:
    """User scope で無効化し、失敗した場合は CLI の診断を返す。"""
    result = claude_common.run_claude(["plugin", "disable", plugin_id, "--scope=user"])
    if result is None or result.returncode != 0:
        return f"disable に失敗: {common.format_cli_error(result)}"
    return None


def restore_disabled_plugins(plugin_ids: list[str]) -> None:
    """導入前の明示的な false を CLI で戻し、失敗しても残りの対象を試す。"""
    if not plugin_ids:
        return
    enabled = read_enabled_plugins()
    changed_ids = [plugin_id for plugin_id in plugin_ids if enabled.get(plugin_id) is not False]
    if not changed_ids:
        return
    failures: dict[str, str] = {}
    for plugin_id in changed_ids:
        if error := disable_plugin(plugin_id):
            failures[plugin_id] = error
    restored = read_enabled_plugins()
    for plugin_id in changed_ids:
        if restored.get(plugin_id) is not False:
            failures.setdefault(plugin_id, "enabledPlugins に false が反映されていません")
    if failures:
        details = " / ".join(f"{plugin_id}: {error}" for plugin_id, error in failures.items())
        raise PluginSettingsError(
            f"導入前の無効設定を復元できませんでした: {details}",
            "表示された原因を解消し、対象IDを `claude plugin disable <ID> --scope=user` で無効化する。",
        )


def read_enabled_plugins() -> dict[str, bool]:
    """設定が未作成・未指定なら空の辞書を返し、読めない設定とは区別する。"""
    path = claude_common.SETTINGS_JSON_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PluginSettingsError(
            f"{path} の JSON を読み取れません: {error}", "settings.json を有効な UTF-8 の JSON に直す。"
        ) from error
    except OSError as error:
        raise PluginSettingsError(f"{path} を読み取れません: {error}", "settings.json の読み取り権限を確認する。") from error
    if not isinstance(data, dict):
        raise PluginSettingsError(
            f"{path} が JSON オブジェクトではありません", "settings.json のトップレベルをオブジェクトに直す。"
        )
    enabled = data.get("enabledPlugins", {})
    if not isinstance(enabled, dict):
        raise PluginSettingsError(f"{path} の enabledPlugins が不正です", "enabledPlugins の値を JSON オブジェクトに直す。")
    result: dict[str, bool] = {}
    for key, value in cast("dict[str, object]", enabled).items():
        if isinstance(key, str) and isinstance(value, bool):
            result[key] = value
    return result
