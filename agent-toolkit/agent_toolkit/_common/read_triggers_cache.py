"""hookの起動をまたいで、読込表の解析結果を本文の内容で再利用する。

hookはツール実行のたびに全エージェント向け文書の読込表を解析し、1回の仕事量が文書数に比例していた。
資料列の解決を後回しにする解析（`defer_material`）は本文・宣言元のパス・解析コードだけで結果が決まるため、
それらの指紋を鍵に保存し、次のhookでは本文の内容が一致する文書だけを再利用する。
本文・パス・解析コードのいずれかが変われば鍵が変わり、その文書を解析し直す。
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import time

from agent_toolkit._common import atomic_file, read_triggers, read_triggers_resolution, state_paths
from agent_toolkit._common.read_triggers_model import ReadTrigger, declaration_issues, record_declaration_issue

_LOG = logging.getLogger(__name__)

# 解析結果を決めるコード。いずれかが変われば保存結果を全て使わない。
_PARSER_MODULES = ("read_triggers.py", "read_triggers_model.py", "read_triggers_resolution.py", "read_triggers_targets.py")
_FORMAT = 1
# 使われなくなった版・プロジェクトの保存結果を残し続けないための保持期間。
_RETENTION_SECONDS = 7 * 24 * 3600


def _parser_fingerprint() -> str:
    digest = hashlib.sha256(str(_FORMAT).encode())
    directory = pathlib.Path(__file__).resolve().parent
    for name in _PARSER_MODULES:
        digest.update((directory / name).read_bytes())
    return digest.hexdigest()[:32]


def _store_path(fingerprint: str, plugin_root: pathlib.Path, host: str, project: str) -> pathlib.Path:
    scope = hashlib.sha256("\0".join((str(plugin_root), host, project)).encode()).hexdigest()[:16]
    return state_paths.state_dir() / "read-trigger-cache" / f"{fingerprint}-{scope}.json"


def _load(path: pathlib.Path) -> dict[str, dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except OSError, ValueError:
        # 壊れた保存結果は使わずに解析し直し、成功した解析で置き換える。
        _LOG.warning("読込表の解析結果の保存を読めないため解析し直す: %s", path, exc_info=True)
        return {}
    return value if isinstance(value, dict) else {}


def _row_values(row: ReadTrigger) -> list:
    return [row.line, row.kind, list(row.operations), row.selector, row.condition, row.material, row.derived_read]


def _row(
    source: pathlib.Path, values: list, plugin_root: pathlib.Path, host: str, project: pathlib.Path | str | None
) -> ReadTrigger:
    line, kind, operations, selector, condition, material, derived = values
    return ReadTrigger(
        source, line, kind, tuple(operations), (), (), selector, condition, material, plugin_root, host, project, derived
    )


def parse_deferred(
    sources: list[pathlib.Path], plugin_root: pathlib.Path, *, host: str, project: str
) -> list[tuple[pathlib.Path, list[ReadTrigger]]]:
    """`parse_tables(..., defer_material=True)`と同じ結果を、本文が変わらない文書では保存結果から返す。

    行単位で除いた宣言の診断も保存し、再利用時に同じ要求の診断として記録し直す。
    保存の失敗は解析結果を変えないため、警告だけを残して続ける。
    """
    fingerprint = _parser_fingerprint()
    store = _store_path(fingerprint, plugin_root, host, project)
    saved = _load(store)
    current: dict[str, dict] = {}
    results: list[tuple[pathlib.Path, list[ReadTrigger]]] = []
    changed = False
    for source in sources:
        resolved = source.resolve()
        text = resolved.read_bytes()
        # 宣言元がplugin外のスキル置き場かどうかで不備行の扱いが変わるため、鍵へ含める。
        side = read_triggers_resolution.user_side_source(resolved)
        key = "\0".join((str(resolved), hashlib.sha256(text).hexdigest(), str(side)))
        entry: dict[str, list] | None = saved.get(key)
        if entry is None:
            before = set(declaration_issues())
            rows = read_triggers.parse_tables(resolved, plugin_root, host=host, project=project, defer_material=True)
            entry = {
                "rows": [_row_values(row) for row in rows],
                "issues": [issue for issue in declaration_issues() if issue not in before],
            }
            changed = True
        else:
            rows = [_row(resolved, values, plugin_root, host, project) for values in entry["rows"]]
            for issue in entry["issues"]:
                record_declaration_issue(issue)
        current[key] = entry
        results.append((resolved, rows))
    if changed or current.keys() != saved.keys():
        try:
            store.parent.mkdir(parents=True, exist_ok=True)
            atomic_file.atomic_write(store, json.dumps(current, ensure_ascii=False))
            _prune(store.parent, keep=store)
        except OSError:
            _LOG.warning("読込表の解析結果を保存できないため、次回も解析し直す: %s", store, exc_info=True)
    return results


def _prune(directory: pathlib.Path, *, keep: pathlib.Path) -> None:
    """保持期間を過ぎた別版・別プロジェクトの保存結果を消す。並行する別版の使用中の結果は期間内なので残る。"""
    limit = time.time() - _RETENTION_SECONDS
    for path in directory.glob("*.json"):
        if path == keep:
            continue
        try:
            if path.stat().st_mtime < limit:
                path.unlink(missing_ok=True)
        except OSError:
            continue
