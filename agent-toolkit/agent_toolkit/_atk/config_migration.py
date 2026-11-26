from __future__ import annotations

import contextlib
import json
import pathlib
import stat
import tomllib
from collections.abc import Generator, Iterable
from typing import Any

from agent_toolkit._atk import config_repositories, research_subscriptions
from agent_toolkit._atk import config_settings as _config_settings
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._common import file_lock, runtime_environment, usage_settings
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common.atomic_file import atomic_write


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


@contextlib.contextmanager
def _conversion(keys: Iterable[str], errors: dict[str, ValueError] | None) -> Generator[None]:
    try:
        yield
    except ValueError as error:
        if errors is None:
            raise
        errors.update(dict.fromkeys(keys, error))


def plan_connection_migration(
    config: dict[str, Any], updates: dict[str, str], *, errors: dict[str, ValueError] | None = None
) -> dict[str, Any]:
    migrated = dict(config)
    additions: dict[str, str] = {}
    if any(key in config for key in _config_settings.CLAUDE_RETIRED_CONNECTION_KEYS):
        keys = ("claude_orchestrator_connections", "claude_worker_connections")
        with _conversion((key for key in keys if key not in config), errors):
            additions.update(
                claude_connection_migration({key: value for key, value in config.items() if isinstance(value, str)})
            )
    if "codex_model_providers" in config:
        codex_keys = {key for key in _config_settings.CONNECTION_DEFAULTS if key.startswith("codex_")}
        # 新しいCodexのキーを明示保存する場合は、移行できない旧設定もその保存で置き換える。
        if not codex_keys & updates.keys():
            raw = config["codex_model_providers"]
            with _conversion((key for key in codex_keys if key not in config), errors):
                additions.update(codex_connection_migration(raw if isinstance(raw, str) else ""))
    for key in _config_settings.RETIRED_CONNECTION_KEYS:
        migrated.pop(key, None)
    migrated.update({key: value for key, value in additions.items() if key not in migrated})
    return migrated


def migrate_fixed_settings(config: dict[str, str]) -> dict[str, str]:
    destinations = _config_settings.RETIRED_FIXED_KEYS
    env = runtime_environment.environment()
    overrides = {
        key: env[name]
        for key in destinations
        if key != "usage_activity_mode" and (name := _config_settings.config_env_name(key)) in env and env[name]
    }
    if not (destinations.keys() & config.keys() or overrides):
        return config
    root = _config_settings.config_dir()
    path_keys = {
        **{f"claude_{kind}_env_file": name for kind, name in _config_settings.CLAUDE_ENV_FILENAMES.items()},
        "weekly_research_x_env_file": _config_settings.X_ENV_FILENAME,
    }
    sources: dict[pathlib.Path, pathlib.Path] = {}
    pending_x = False
    for key, filename in path_keys.items():
        if raw := overrides.get(key, config.get(key)):
            try:
                caller = runtime_environment.current()
                cwd = caller.cwd if caller is not None else pathlib.Path.cwd()
                path = pathlib.Path(raw)
                if caller is not None and path.parts and path.parts[0] == "~":
                    path = runtime_environment.home().joinpath(*path.parts[1:])
                source = cwd / path.expanduser()
                source.resolve()
            except OSError, RuntimeError, ValueError, TypeError:
                raise _next_action.ActionableError(
                    f"旧設定{key}のファイルを解決できない",
                    next_action=f"envファイルを設定ディレクトリの{filename}へ移して旧指定を解除する",
                ) from None
            sources[root / filename] = source
    resolved_sources = {target: source.resolve() for target, source in sources.items()}
    if len(set(resolved_sources.values())) != len(sources) or any(
        source == (root / filename).resolve()
        for target, source in resolved_sources.items()
        for filename in path_keys.values()
        if target.name != filename
    ):
        raise _next_action.ActionableError(
            "旧設定が複数の認証種別で同じenvファイルを参照している",
            next_action="設定ディレクトリで認証の種別ごとにenvファイルを分け、対応する旧指定を解除する",
        )
    if sources:
        root.mkdir(parents=True, exist_ok=True)
        with (root / ".auth-env-migration.lock").open("a+b") as lock:
            file_lock.acquire_lock(lock)
            try:
                for target, source in sources.items():
                    if not _move_auth_file(source, target, defer_missing=target.name == _config_settings.X_ENV_FILENAME):
                        pending_x = True
            finally:
                file_lock.release_lock(lock)

    inventory_key = "claude_reset_inventory"
    if inventory := overrides.get(inventory_key, config.get(inventory_key)):
        usage_settings.save_claude_reset_inventory(inventory, migrate=True)

    for key, limit in (
        ("weekly_research_x_max_posts", _config_settings.X_MAX_POSTS),
        ("weekly_research_x_max_users", _config_settings.X_MAX_USERS),
    ):
        value = config.get(key)
        if isinstance(value, str) and value.isdecimal() and int(value) < limit:
            _outcome.report_warning(
                f"廃止した{key}より固定上限（{limit}）が高い",
                next_action="X側の支出上限を確認してください。取得を止めるにはweekly_research_x_apiをfalseにしてください。",
            )
    pending = {"weekly_research_x_env_file"} if pending_x else set()
    return {key: value for key, value in config.items() if key not in destinations or key in pending}


def _move_auth_file(source: pathlib.Path, target: pathlib.Path, *, defer_missing: bool) -> bool:
    try:
        if target.is_symlink() or source.is_symlink():
            raise ValueError("移行に使う認証ファイルをシンボリックリンクにしない")
        if source.resolve() == target.resolve():
            return True
        try:
            source_mode = source.stat().st_mode
        except FileNotFoundError:
            if target.is_file():
                return True
            if defer_missing:
                return False
            raise
        if not stat.S_ISREG(source_mode):
            raise ValueError("移行元は通常ファイルにし、移行先にシンボリックリンクを置かない")
        # 内容を比較するのは、別ファイルシステムや保存直後の中断から再開するため。  # pyfltr: allow-comment
        content = source.read_bytes()
        try:
            target_mode = target.stat().st_mode
        except FileNotFoundError:
            atomic_write(target, content, fsync=True)
        else:
            if not stat.S_ISREG(target_mode) or target.read_bytes() != content:
                raise ValueError("固定名に別内容のファイルがあるため上書きしない")
        source.unlink()
        return True
    except OSError, ValueError:
        raise _next_action.ActionableError(
            f"認証envを設定ディレクトリの{target.name}へ移行できない",
            next_action="旧ファイルを確認し、固定名に別のファイルがある場合は残す認証を選んでから旧指定を解除する",
        ) from None


def compare_shared_values(local: dict[str, Any], shared: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in sorted((local.keys() | shared.keys()) & _config_settings.SHARED_SETTING_KEYS):
        if key not in shared:
            result[key] = "local_only"
        elif key not in local:
            result[key] = "shared_only"
        else:
            result[key] = "equal" if local[key] == shared[key] else "different"
    return result


def merge_remote_hosts(*values: str) -> str:
    hosts = dict.fromkeys(host for value in values for host in _config_settings.parse_remote_hosts(value))
    return json.dumps(list(hosts), ensure_ascii=False)


def remote_hosts_environment() -> tuple[str, str]:
    env = runtime_environment.environment()
    name = _config_settings.config_env_name("remote_hosts")
    if value := env.get(name):
        return value, name
    names = tuple(_config_settings.config_env_name(key) for key in _config_settings.LEGACY_REMOTE_HOSTS_KEYS)
    legacy = {name: env[name] for name in names if env.get(name)}
    if not legacy:
        return "", ""
    source = " / ".join(legacy)
    try:
        return merge_remote_hosts(*legacy.values()), source
    except ValueError as error:
        raise _next_action.ActionableError(
            f"旧環境変数で指定したSSH接続先の書式が不正です: {source}。{error}",
            next_action=f"{name}へJSON文字列配列で接続先を指定する",
        ) from error


_RESEARCH_REPOS = "weekly_research_repos"
_RESEARCH_SUBSCRIPTIONS = {"accounts": "weekly_research_x_accounts", "queries": "weekly_research_x_queries"}


LEGACY_SETTING_GROUPS = {
    "connection": tuple(_config_settings.RETIRED_CONNECTION_KEYS),
    "process-loop-repositories": ("process_loop_repositories",),
    "research-repositories": (_RESEARCH_REPOS,),
    "model": tuple(_config_settings.RETIRED_MODEL_KEYS),
    "remote-hosts": _config_settings.LEGACY_REMOTE_HOSTS_KEYS,
    "retired": ("codex_auto_reset", "usage_auto_allocation", *_config_settings.RETIRED_SERVE_HOME_KEYS),
    "fixed-state": tuple(_config_settings.RETIRED_FIXED_KEYS),
    "research-subscriptions": tuple(_RESEARCH_SUBSCRIPTIONS.values()),
}


def _project_research_repositories(config: dict[str, str]) -> None:
    try:
        old_repos = _config_settings.parse_string_list(config.get(_RESEARCH_REPOS, "[]"))
        if not old_repos:
            return
        repositories = config_repositories.parse(config.get("repositories", "{}"), check_paths=False)
        for value in old_repos:
            repo_id, path = config_repositories.identifier(value)
            repositories.setdefault(repo_id, {"edit": "awi", **({"path": path} if path else {})})
        config["repositories"] = json.dumps(repositories, ensure_ascii=False)
    except (TypeError, ValueError) as error:
        raise _next_action.ActionableError(
            f"旧調査リポジトリを移行できない: {error}",
            next_action="設定ファイルの旧調査リポジトリを有効なリポジトリ一覧に直して再実行する",
        ) from error


def _migrate_research_subscriptions(config: dict[str, str]) -> None:
    try:
        initial = research_subscriptions.parse(
            {field: json.loads(config[key]) for field, key in _RESEARCH_SUBSCRIPTIONS.items() if key in config}
        )
    except (TypeError, ValueError) as error:
        raise _next_action.ActionableError(
            f"旧調査設定を移行できない: {error}",
            next_action="設定ファイルの旧調査設定を確認し、有効なXアカウント・検索語に直して再実行する",
        ) from error
    research_subscriptions.migrate({field: values for field, values in initial.items() if values})
    for key in _config_settings.RETIRED_RESEARCH_KEYS:
        config.pop(key, None)


def plan_legacy_migration(
    config: dict[str, str], *, updates: dict[str, str] | None = None, preview_errors: dict[str, ValueError] | None = None
) -> dict[str, str]:
    migrated = _legacy_values(config, updates=updates, errors=preview_errors)
    if _RESEARCH_REPOS in migrated:
        with _conversion(("repositories",), preview_errors):
            _project_research_repositories(migrated)
    if preview_errors is None:
        return _apply_legacy_side_effects(migrated)
    return migrated


def _legacy_values(
    config: dict[str, Any], *, updates: dict[str, str] | None = None, errors: dict[str, ValueError] | None = None
) -> dict[str, str]:
    changes = dict(updates or {})
    migrated = plan_connection_migration(config, changes, errors=errors)
    with _conversion(("repositories",), errors):
        migrated = config_repositories.migrate(migrated)
    old_hosts = [migrated.pop(key) for key in _config_settings.LEGACY_REMOTE_HOSTS_KEYS if key in migrated]
    for key in LEGACY_SETTING_GROUPS["retired"]:
        migrated.pop(key, None)
    if old_hosts and "remote_hosts" not in migrated and "remote_hosts" not in changes:
        with _conversion(("remote_hosts",), errors):
            if any(not isinstance(value, str) for value in old_hosts):
                raise ValueError("旧SSH接続先はJSON文字列配列を表す文字列で指定する")
            migrated["remote_hosts"] = merge_remote_hosts(*old_hosts)
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
            with _conversion((target,), errors):
                engine_key = f"{usage}_engine"
                default_order = _config_settings.LEGACY_ENGINE_DEFAULTS[engine_key]
                if stage == "execute" and "base_engine" in config:
                    default_order = config["base_engine"]
                order = config.get(engine_key, default_order)
                if not isinstance(order, str):
                    raise ValueError(f"旧設定{engine_key}の候補順は文字列で指定する")
                engines = _config_settings.parse_engine_order(order)
                candidates = []
                for engine in engines:
                    key = f"{engine}_{tier}_tier_model"
                    value = config.get(key, _config_settings.LEGACY_TIER_DEFAULTS[key])
                    if engine == "opencode" and not value:
                        continue
                    if not isinstance(value, str) or not value:
                        raise ValueError(f"旧設定{key}は空でない文字列で指定する")
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


def _apply_legacy_side_effects(config: dict[str, str]) -> dict[str, str]:
    migrated = migrate_fixed_settings(config)
    if _config_settings.RETIRED_RESEARCH_KEYS.keys() & migrated.keys():
        _migrate_research_subscriptions(migrated)
    return migrated


def has_legacy_settings(config: dict[str, Any]) -> bool:
    env = runtime_environment.environment()
    return any(key in config for keys in LEGACY_SETTING_GROUPS.values() for key in keys) or any(
        env.get(_config_settings.config_env_name(key))
        for key in _config_settings.RETIRED_FIXED_KEYS
        if key != "usage_activity_mode"
    )


def migrate_legacy_settings() -> None:
    """旧モデル・接続設定と廃止した設定キーを移行する。環境変数の値は設定JSONへ保存しない。  pyfltr: allow-comment"""
    config = _config_settings.load_config_strict()
    if not has_legacy_settings(config):
        return
    migrated = plan_legacy_migration(config)
    if migrated != config:
        _config_settings.save_config(migrated)
        if "codex_auto_reset" in config:
            _outcome.report_warning(
                "廃止したcodex_auto_resetを設定から削除した。利用枠の消費許可は作成していない",
                next_action="明示許可はusage_reset_permissionsで確認してください。",
            )


def legacy_serve_config_path() -> pathlib.Path:
    """旧`serve.toml`の場所を返す。`AGENT_TOOLKIT_SERVE_CONFIG`があればその値、無ければ設定ディレクトリの`serve.toml`。"""
    explicit = runtime_environment.environment().get(_config_settings.SERVE_CONFIG_ENV)
    return pathlib.Path(explicit) if explicit else _config_settings.config_dir() / "serve.toml"


def legacy_serve_values(
    loaded: dict[str, Any], path: pathlib.Path, *, errors: dict[str, ValueError] | None = None
) -> dict[str, str]:
    known_sections = {section for section, _ in _config_settings.LEGACY_SERVE_KEYS if section is not None}
    values: dict[str, str] = {}
    unknown: list[str] = []
    for name, item in loaded.items():
        if name in known_sections:
            if not isinstance(item, dict):
                targets = (target for (section, _), target in _config_settings.LEGACY_SERVE_KEYS.items() if section == name)
                with _conversion(targets, errors):
                    raise ValueError(f"{path}の{name}がテーブルではない")
                continue
            entries = [((name, key), value) for key, value in item.items()]
        else:
            entries = [((None, name), item)]
        for (section, key), value in entries:
            target = _config_settings.LEGACY_SERVE_KEYS.get((section, key))
            label = key if section is None else f"{section}.{key}"
            if target is None:
                if (section, key) not in {("sessions", "claude_home"), ("sessions", "codex_home")}:
                    unknown.append(label)
                continue
            with _conversion((target,), errors):
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
                if target == "remote_hosts" and target in values:
                    converted = merge_remote_hosts(values[target], converted)
                values[target] = converted
    if unknown and errors is None:
        _outcome.report_warning(
            f"{path}の移行先の無いキーをスキップした: {', '.join(sorted(unknown))}",
            next_action="必要な値は移行後の設定と元ファイルを見比べる",
        )
    return values


def _read_legacy_serve(path: pathlib.Path, *, errors: dict[str, ValueError] | None = None) -> dict[str, str] | None:
    retry = f"{path}の記述を直すか、移行しない項目を削除してから再実行する"
    try:
        loaded = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise _next_action.ActionableError(
            f"旧設定ファイル{path}を読めないため移行できない: {error}", next_action=retry
        ) from error
    try:
        return legacy_serve_values(loaded, path, errors=errors)
    except ValueError as error:
        raise _next_action.ActionableError(f"旧設定ファイルを移行できない: {error}", next_action=retry) from error


def _legacy_serve_additions(config: dict[str, Any], values: dict[str, str]) -> dict[str, str]:
    supplied = set(config)
    if supplied.intersection(_config_settings.LEGACY_REMOTE_HOSTS_KEYS):
        supplied.add("remote_hosts")
    return {key: value for key, value in values.items() if key not in supplied}


def preview_local_settings(config: dict[str, Any]) -> tuple[dict[str, str], dict[str, ValueError]]:
    errors: dict[str, ValueError] = {}
    migrated = plan_legacy_migration(config, preview_errors=errors)
    serve_errors: dict[str, ValueError] = {}
    targets = set(_config_settings.LEGACY_SERVE_KEYS.values())
    with _conversion(targets, serve_errors):
        values = _read_legacy_serve(legacy_serve_config_path(), errors=serve_errors)
        migrated.update(
            _legacy_serve_additions(migrated, {key: value for key, value in (values or {}).items() if key not in serve_errors})
        )
    errors.update({key: error for key, error in serve_errors.items() if key not in config and key not in migrated})
    check_legacy_environment(errors=errors)
    return {key: value for key, value in migrated.items() if isinstance(value, str)}, errors


def migrate_serve_settings() -> None:
    """旧`serve.toml`の値を統一設定へ移行し、旧ファイルを`<旧ファイル名>.migrated`へ改名する。

    統一設定に既にあるキーは上書きせず、無いキーだけを補う。改名後は移行元が無くなるため、
    再実行しても移行済みの値を旧ファイルで上書きしない。読めない・形式不正・保存失敗・改名失敗では
    旧ファイルと統一設定の既存内容を変えずに`ActionableError`を送出し、直して再実行できる状態を保つ
    （改名だけが失敗した場合は保存済みの値を保ち、再実行で改名だけを行う）。
    """
    path = legacy_serve_config_path()
    values = _read_legacy_serve(path)
    if values is None:
        return
    config = _config_settings.load_config_strict()
    additions = _legacy_serve_additions(config, values)
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


def check_legacy_environment(*, errors: dict[str, ValueError] | None = None) -> None:
    with _conversion(("repositories",), errors):
        _config_settings.check_repository_environment()
    found = sorted(
        key
        for key in _config_settings.RETIRED_MODEL_KEYS
        if _config_settings.config_env_name(key) in runtime_environment.environment()
    )
    if found:
        replacements = []
        targets: set[str] = set()
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
            targets.update(f"{stage}_model" for stage in stages)
            replacements.append(
                f"{_config_settings.config_env_name(key)} → "
                f"{', '.join(_config_settings.config_env_name(f'{stage}_model') for stage in stages)}"
            )
        with _conversion(targets, errors):
            raise _next_action.ActionableError(
                "廃止した旧モデル設定の環境変数が設定されています: "
                f"{', '.join(_config_settings.config_env_name(key) for key in found)}",
                next_action="旧環境変数を解除し、工程別の環境変数へ置き換えて再実行する: " + "; ".join(replacements),
            )

    _check_retired_environment(
        "接続",
        _config_settings.RETIRED_CONNECTION_KEYS,
        "旧環境変数を解除し、接続設定の新しい環境変数へ置き換えて再実行する",
        errors=errors,
    )
    _check_retired_environment(
        "調査",
        {key: (target,) for key, target in _config_settings.RETIRED_RESEARCH_KEYS.items()},
        "値を控えて旧環境変数を解除し、次の操作で移してから再実行する",
        errors=errors,
    )

    key = "usage_auto_allocation"
    retired = _config_settings.config_env_name(key)
    if retired in runtime_environment.environment():
        with _conversion((key,), errors):
            raise _next_action.ActionableError(
                f"廃止した設定の環境変数が設定されています: {retired}",
                next_action="旧環境変数を解除して再実行する。候補順は工程別の`*_model`キーで指定する",
            )


def _check_retired_environment(
    kind: str, destinations: dict[str, tuple[str, ...]], guidance: str, *, errors: dict[str, ValueError] | None = None
) -> None:
    env = runtime_environment.environment()
    found = [key for key in destinations if env.get(_config_settings.config_env_name(key), "")]
    if not found:
        return
    replacements = [
        f"{_config_settings.config_env_name(key)} → "
        + ", ".join(
            _config_settings.config_env_name(target) if target in _config_settings.MUTABLE_KEY_DEFAULTS else target
            for target in destinations[key]
        )
        for key in found
    ]
    targets: set[str] = set()
    for key in found:
        current = set(destinations[key]).intersection(_config_settings.MUTABLE_KEY_DEFAULTS)
        targets.update(current or {key})
    with _conversion(targets, errors):
        raise _next_action.ActionableError(
            f"廃止した旧{kind}設定の環境変数が設定されています: "
            + ", ".join(_config_settings.config_env_name(key) for key in found),
            next_action=f"{guidance}: " + "; ".join(replacements),
        )
