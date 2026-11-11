"""旧モデル・接続設定とserve.tomlの移行。"""

from __future__ import annotations

import json
import os
import pathlib
import tomllib
from typing import Any

from agent_toolkit._atk import config_repositories
from agent_toolkit._atk import config_settings as _config_settings
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._common import next_action as _next_action


def claude_connection_migration(config: dict[str, str]) -> dict[str, str]:
    """旧Claude接続設定の保存値から、移行先のキーと値を返す。

    旧実効方針が`api-credits`（委譲先API優先）ならenvファイルをクレジット、それ以外（`pay-as-you-go`・`api-only`）なら
    従量課金へ移す。旧キーの組み合わせが表した役割別の方針を、移した種別を使う使用順へ書き換える。
    envファイルが無い場合はAPIの種別を使用順から除き、それで空になる役割は書かずに初期値を使わせる。
    """
    mode = config.get("claude_api_mode", "")
    mode_values = {"pay-as-you-go": ("true", "false"), "api-credits": ("false", "true"), "api-only": ("true", "true")}
    derived = mode_values.get(mode, ("true", "false"))
    orchestrator_api = config.get("claude_orchestrator_api_only", derived[0]) == "true"
    worker_api_first = config.get("claude_worker_api_first", derived[1]) == "true"
    orchestrator_api_only = mode == "api-only" and "claude_orchestrator_api_only" not in config
    worker_api_only = mode == "api-only" and "claude_worker_api_first" not in config
    api = "credits" if not worker_api_only and worker_api_first else "payg"
    orchestrator = (api,) if orchestrator_api_only or orchestrator_api else ("subscription",)
    if worker_api_only:
        worker: tuple[str, ...] = (api,)
    elif worker_api_first:
        worker = (api, "subscription")
    else:
        worker = ("subscription", api)
    env_file = config.get("claude_api_env_file", "")
    result: dict[str, str] = {}
    if env_file:
        result[f"claude_{api}_env_file"] = env_file
    else:
        orchestrator = tuple(kind for kind in orchestrator if kind != api)
        worker = tuple(kind for kind in worker if kind != api)
    if orchestrator:
        result["claude_orchestrator_connections"] = ",".join(orchestrator)
    if worker:
        result["claude_worker_connections"] = ",".join(worker)
    return result


def codex_connection_migration(value: str) -> dict[str, str]:
    """旧`codex_model_providers`の保存値から、移行先のキーと値を返す。

    `openai`はサブスクリプション、それ以外のprovider IDは1つに限り従量課金へ移し、元の順序の種別列を両役割へ書く。
    アカウント種別は参照しない。`openai`以外が2つ以上ある場合は移行先を一意に決められないため`ActionableError`を送出する。
    """
    try:
        providers = _config_settings.parse_codex_provider_candidates(value)
    except ValueError as error:
        raise _next_action.ActionableError(
            f"旧設定codex_model_providersの値が不正で移行できません（値: {value}）。{error}",
            next_action=(
                "設定ファイルからcodex_model_providersを削除し、新しい接続設定キーを`atk config set`で設定して再実行する"
            ),
        ) from error
    others = [provider for provider in providers if provider != "openai"]
    if len(others) >= 2:
        raise _next_action.ActionableError(
            f"旧設定codex_model_providersにopenai以外のprovider IDが2つ以上あり、自動移行できません（値: {value}）",
            next_action=(
                "`atk config set codex_credits_provider <ID>`・`atk config set codex_payg_provider <ID>`と"
                "`atk config set codex_worker_connections <種別列>`などで新しい接続設定キーを設定する。"
                "新しいCodexの接続設定キーを1つ設定すると旧設定は削除される"
            ),
        )
    if not providers:
        return {}
    kinds = ",".join("subscription" if provider == "openai" else "payg" for provider in providers)
    result = {"codex_orchestrator_connections": kinds, "codex_worker_connections": kinds}
    if others:
        result["codex_payg_provider"] = others[0]
    return result


def plan_connection_migration(config: dict[str, Any], updates: dict[str, str]) -> dict[str, Any]:
    """旧接続設定キーを移行先へ書き換え、旧キーを除いた設定を返す。既に保存した移行先のキーは上書きしない。"""
    migrated = dict(config)
    additions: dict[str, str] = {}
    if any(key in config for key in _config_settings.CLAUDE_RETIRED_CONNECTION_KEYS):
        additions.update(claude_connection_migration({key: value for key, value in config.items() if isinstance(value, str)}))
    if "codex_model_providers" in config:
        codex_keys = {key for key in _config_settings.CONNECTION_DEFAULTS if key.startswith("codex_")}
        # 新しいCodexのキーを明示保存する場合は、移行できない旧設定もその保存で置き換える。
        if not codex_keys & updates.keys():
            raw = config["codex_model_providers"]
            additions.update(codex_connection_migration(raw if isinstance(raw, str) else ""))
    for key in _config_settings.RETIRED_CONNECTION_KEYS:
        migrated.pop(key, None)
    migrated.update({key: value for key, value in additions.items() if key not in migrated})
    return migrated


def plan_legacy_migration(config: dict[str, str], *, updates: dict[str, str] | None = None) -> dict[str, str]:
    """旧設定を合成し、明示保存値を同じ原子的更新へまとめる。

    読取時は全工程を検証する。明示保存では候補未設定の他工程を空のまま保持し、
    その後のsetで修復できるようにする。新しいCLI値として空候補は受理しない。
    """
    changes = dict(updates or {})
    migrated = config_repositories.migrate(plan_connection_migration(config, changes))
    migrated.update(changes)
    if not any(key in config for key in _config_settings.RETIRED_MODEL_KEYS):
        return migrated
    stages = {
        "execute": ("execute", "high"),
        "review": ("review", "medium"),
        "review_fast": ("review", "low"),
        "chores": ("chores", "medium"),
        "chores_fast": ("chores", "low"),
        "orchestrate": ("orchestrate", "high"),
    }
    if any(key in config for key in (*_config_settings.LEGACY_TIER_DEFAULTS, *_config_settings.LEGACY_ENGINE_DEFAULTS)):
        for stage, (usage, tier) in stages.items():
            target = f"{stage}_model"
            if updates is not None and target in updates:
                continue
            engine_key = f"{usage}_engine"
            default_order = _config_settings.LEGACY_ENGINE_DEFAULTS[engine_key]
            if stage == "execute" and "base_engine" in config:
                default_order = config["base_engine"]
            engines = _config_settings.parse_engine_order(config.get(engine_key, default_order))
            candidates = []
            for engine in engines:
                key = f"{engine}_{tier}_tier_model"
                value = config.get(key, _config_settings.LEGACY_TIER_DEFAULTS[key])
                if engine == "opencode" and not value:
                    continue
                if not value:
                    raise ValueError(f"旧設定{key}が空です。移行前に候補モデルを指定する")
                candidates.append(f"{engine}:{value}")
            migrated[target] = ",".join(candidates)
            if not candidates:
                if updates is not None:
                    continue
                raise _next_action.ActionableError(
                    f"旧設定から{target}へ合成するモデル候補がありません。受理可能書式の候補列が必要です",
                    next_action=(
                        f"`atk config set {target} <候補列>`で候補を明示するか、"
                        "`atk config apply-preset <プリセット名>`で6工程を一括設定して再実行する"
                    ),
                )
            _config_settings.validate_mutable_setting(target, migrated[target])
    return {key: value for key, value in migrated.items() if key not in _config_settings.RETIRED_MODEL_KEYS}


def migrate_legacy_settings() -> None:
    """旧モデル設定と旧接続設定の保存値を移行先へ一括移行する。環境変数は保存しない。"""
    config = _config_settings.load_config_strict()
    legacy_keys = (*_config_settings.RETIRED_MODEL_KEYS, *_config_settings.RETIRED_CONNECTION_KEYS, "process_loop_repositories")
    if not any(key in config for key in legacy_keys):
        return
    migrated = plan_legacy_migration(config)
    if migrated != config:
        _config_settings.save_config(migrated)


def legacy_serve_config_path() -> pathlib.Path:
    """旧`serve.toml`の場所を返す。`AGENT_TOOLKIT_SERVE_CONFIG`があればその値、無ければ設定ディレクトリの`serve.toml`。"""
    explicit = os.environ.get(_config_settings.SERVE_CONFIG_ENV)
    return pathlib.Path(explicit) if explicit else _config_settings.config_dir() / "serve.toml"


def legacy_serve_values(loaded: dict[str, Any], path: pathlib.Path) -> dict[str, str]:
    """旧`serve.toml`の内容を移行先のキーと保存形式の値へ変換する。未知キーと型不正は`ValueError`を送出する。"""
    known_sections = {section for section, _ in _config_settings.LEGACY_SERVE_KEYS if section is not None}
    values: dict[str, str] = {}
    unknown: list[str] = []
    for name, item in loaded.items():
        if name in known_sections:
            if not isinstance(item, dict):
                raise ValueError(f"{path}の{name}がテーブルではない")
            entries = [((name, key), value) for key, value in item.items()]
        else:
            entries = [((None, name), item)]
        for (section, key), value in entries:
            target = _config_settings.LEGACY_SERVE_KEYS.get((section, key))
            label = key if section is None else f"{section}.{key}"
            if target is None:
                unknown.append(label)
                continue
            if target == "serve_port":
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError(f"{path}の{label}が整数ではない")
                converted = str(value)
            elif target in _config_settings.SERVE_HOSTS_KEYS:
                if not isinstance(value, list) or any(not isinstance(host, str) for host in value):
                    raise ValueError(f"{path}の{label}が文字列の配列ではない")
                converted = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, str) and value.strip():
                converted = value
            else:
                raise ValueError(f"{path}の{label}が空でない文字列ではない")
            try:
                _config_settings.validate_mutable_setting(target, converted)
            except ValueError as error:
                raise ValueError(f"{path}の{label}の値が不正である。{error}") from error
            values[target] = converted
    if unknown:
        raise ValueError(f"{path}に移行先の無いキーがある: {', '.join(sorted(unknown))}")
    return values


def migrate_serve_settings() -> None:
    """旧`serve.toml`の値を統一設定へ移行し、旧ファイルを`<旧ファイル名>.migrated`へ改名する。

    統一設定に既にあるキーは上書きせず、無いキーだけを補う。改名後は移行元が無くなるため、
    再実行しても移行済みの値を旧ファイルで上書きしない。読めない・形式不正・保存失敗・改名失敗では
    旧ファイルと統一設定の既存内容を変えずに`ActionableError`を送出し、直して再実行できる状態を保つ
    （改名だけが失敗した場合は保存済みの値を保ち、再実行で改名だけを行う）。
    """
    path = legacy_serve_config_path()
    retry = f"{path}の記述を直すか、移行しない項目を削除してから再実行する"
    try:
        loaded = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise _next_action.ActionableError(
            f"旧設定ファイル{path}を読めないため移行できない: {error}", next_action=retry
        ) from error
    try:
        values = legacy_serve_values(loaded, path)
    except ValueError as error:
        raise _next_action.ActionableError(f"旧設定ファイルを移行できない: {error}", next_action=retry) from error
    config = _config_settings.load_config_strict()
    additions = {key: value for key, value in values.items() if key not in config}
    if additions:
        try:
            _config_settings.save_config({**config, **additions})
        except OSError as error:
            raise _next_action.ActionableError(
                f"旧設定ファイル{path}の値を{_config_settings.config_file_path()}へ保存できない: {error}",
                next_action=f"{_config_settings.config_file_path().parent}へ書き込めるようにして再実行する",
            ) from error
    target = path.with_name(f"{path.name}.migrated")
    try:
        path.replace(target)
    except OSError as error:
        raise _next_action.ActionableError(
            f"旧設定ファイル{path}を{target}へ改名できない（値の移行は完了済み）: {error}",
            next_action=f"{path}を改名できるようにするか削除して再実行する",
        ) from error
    migrated = ", ".join(additions) if additions else "なし（全キーが保存済み）"
    _outcome.report_warning(
        f"旧設定ファイル{path}の値を`atk config`へ移行し、元のファイルを{target}へ改名した。移行したキー: {migrated}",
        next_action="対応不要。以後の確認と変更は`atk config get serve_<項目>`・`atk config set serve_<項目> <値>`で行う",
    )


def check_legacy_environment() -> None:
    """廃止キーの環境変数があれば、対応する工程別環境変数を案内する。"""
    _config_settings.check_repository_environment()
    found = sorted(key for key in _config_settings.RETIRED_MODEL_KEYS if _config_settings.config_env_name(key) in os.environ)
    if found:
        replacements = []
        for key in found:
            if "orchestrate" in key:
                stages = ("orchestrate",)
            elif "medium" in key:
                stages = ("review", "chores")
            elif "low" in key:
                stages = ("review_fast", "chores_fast")
            elif key == "review_engine":
                stages = ("review", "review_fast")
            elif key == "chores_engine":
                stages = ("chores", "chores_fast")
            elif "high" in key:
                stages = ("execute", "orchestrate")
            else:
                stages = ("execute",)
            replacements.append(
                f"{_config_settings.config_env_name(key)} → "
                f"{', '.join(_config_settings.config_env_name(f'{stage}_model') for stage in stages)}"
            )
        raise _next_action.ActionableError(
            "廃止した旧モデル設定の環境変数が設定されています: "
            f"{', '.join(_config_settings.config_env_name(key) for key in found)}",
            next_action="旧環境変数を解除し、工程別の環境変数へ置き換えて再実行する: " + "; ".join(replacements),
        )
    # 空文字列は他の設定キーと同じく未設定として扱う。
    connection_found = [
        key for key in _config_settings.RETIRED_CONNECTION_KEYS if os.environ.get(_config_settings.config_env_name(key), "")
    ]
    if connection_found:
        replacements = [
            f"{_config_settings.config_env_name(key)} → "
            f"{', '.join(_config_settings.config_env_name(target) for target in targets)}"
            for key in connection_found
            for targets in [_config_settings.RETIRED_CONNECTION_KEYS[key]]
        ]
        raise _next_action.ActionableError(
            "廃止した旧接続設定の環境変数が設定されています: "
            + ", ".join(_config_settings.config_env_name(key) for key in connection_found),
            next_action="旧環境変数を解除し、接続設定の新しい環境変数へ置き換えて再実行する: " + "; ".join(replacements),
        )
