"""メタデータ走査とローカル容量レポート。整理コマンドは実行しない。"""

import csv
import dataclasses
import datetime
import io
import json
import os
import pathlib
import shlex
import stat
import subprocess
import tempfile

from pytools._internal.common import format_cli_error, process_scope

_FIELDS = ["time", "target", "conditions", "run_status", "path", "kind", "size", "status"]
_DEFAULT_NAMES = ("Pictures", "Photos", "OneDrive", "Dropbox", "iCloudDrive")
_CLOUD_VARIABLES = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")


@dataclasses.dataclass
class _Node:
    path: str
    parent: str | None
    kind: str = "unknown"
    size: int | None = None
    status: str = "success"
    detail: str = ""
    delta: int | None = None


def write_report(target: pathlib.Path, output: pathlib.Path, excludes: list[pathlib.Path]) -> int:
    """入力を検証し、HTMLとBOM付きCSV履歴を保存する。部分取得は終了1。"""
    target = target.expanduser().absolute()
    # resolveでリンクを消してしまう前に、指定経路自体の追跡境界を確認する。
    for part in (target, *target.parents):
        if _is_link(part.lstat()):
            raise ValueError(f"targetにリンク・reparse pointは指定できません: {part}")
    target = target.resolve(strict=True)
    if not target.is_dir():
        raise ValueError(f"targetはディレクトリを指定してください: {target}")
    output = output.expanduser().resolve()
    if target.is_relative_to(output):
        raise ValueError("出力先はtargetと別のディレクトリ、またはtargetの子ディレクトリにしてください")
    explicit = []
    for exclude in excludes:
        path = exclude.expanduser()
        explicit.append((path if path.is_absolute() else target / path).resolve(strict=True))
    defaults = [pathlib.Path.home() / name for name in _DEFAULT_NAMES]
    for name in _CLOUD_VARIABLES:
        if os.environ.get(name):
            path = pathlib.Path(os.environ[name]).expanduser()
            if not path.is_absolute():
                raise ValueError(f"環境変数{name}には絶対パスを指定してください")
            defaults.append(path)
    excluded = sorted({output, *explicit, *(p.resolve() for p in defaults if p.exists())}, key=str)
    conditions = json.dumps({"metadata": 1, "exclude": [str(p) for p in excluded]}, ensure_ascii=True, sort_keys=True)
    history = output / "history.csv"
    previous = _read_history(history, str(target), conditions)
    nodes = _scan(target, excluded)
    failed = any(node.status in {"unavailable", "missing", "partial"} for node in nodes)
    for node in nodes:
        if not failed and node.status == "success" and node.path in previous and node.size is not None:
            node.delta = node.size - previous[node.path]
    candidate, candidate_reason = _uv_candidate(target, nodes)
    data = {
        "time": datetime.datetime.now(datetime.UTC).isoformat(),
        "target": str(target),
        "conditions": conditions,
        "excluded": [str(p) for p in excluded],
        "status": "partial" if failed else "success",
        "nodes": [dataclasses.asdict(node) for node in nodes],
        "previous": bool(previous),
        "candidate": candidate,
        "candidate_reason": candidate_reason,
    }
    payload = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = pathlib.Path(__file__).with_suffix(".html").read_text(encoding="utf-8")
    output.mkdir(parents=True, exist_ok=True)
    _save(output / "report.html", template.replace("__REPORT_DATA__", payload).encode("utf-8"))
    # 読み取った履歴を置換保存し、途中の失敗でCSVの末尾を破損させない。
    existing = history.read_text(encoding="utf-8-sig") if history.exists() else ""
    stream = io.StringIO(newline="")
    stream.write(existing)
    writer = csv.writer(stream)
    if not existing:
        writer.writerow(_FIELDS)
    for node in nodes:
        writer.writerow(
            [
                data["time"],
                str(target),
                conditions,
                data["status"],
                node.path,
                node.kind,
                node.size if node.size is not None else "",
                node.status,
            ]
        )
    _save(history, stream.getvalue().encode("utf-8-sig"))
    return int(failed)


def _is_link(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _scan(target: pathlib.Path, excluded: list[pathlib.Path]) -> list[_Node]:
    nodes: list[_Node] = []
    root_device = target.lstat().st_dev
    pending: list[tuple[pathlib.Path, str | None]] = [(target, None)]
    while pending:
        path, parent = pending.pop()
        node = _Node(str(path), parent)
        nodes.append(node)
        if any(path.is_relative_to(item) for item in excluded):
            node.status, node.detail = "excluded", "指定・既定の除外"
            continue
        try:
            metadata = path.lstat()
            if _is_link(metadata):
                node.status, node.detail = "excluded", "リンク・junction・reparse point"
            elif metadata.st_dev != root_device:
                node.status, node.detail = "excluded", "別ファイルシステム"
            elif stat.S_ISREG(metadata.st_mode):
                node.kind, node.size = "file", metadata.st_size
            elif stat.S_ISDIR(metadata.st_mode):
                node.kind, node.size = "directory", 0
                with os.scandir(path) as entries:
                    pending.extend((pathlib.Path(entry.path), str(path)) for entry in entries)
            else:
                node.status, node.detail = "excluded", "通常ファイル・ディレクトリ以外"
        except FileNotFoundError as exc:
            node.status, node.detail = "missing", str(exc)
            node.size = None
        except OSError as exc:
            node.status, node.detail = "unavailable", str(exc)
            node.size = None
    by_path = {node.path: node for node in nodes}
    for node in reversed(nodes):
        if node.parent is not None:
            parent = by_path[node.parent]
            parent.size = (parent.size or 0) + (node.size or 0)
            if node.status in {"missing", "unavailable", "partial"}:
                parent.status = "partial"
    return nodes


def _read_history(path: pathlib.Path, target: str, conditions: str) -> dict[str, int]:
    if not path.exists():
        return {}
    previous: dict[str, int] = {}
    last_time = ""
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            if reader.fieldnames != _FIELDS:
                raise ValueError("列名が一致しません")
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("列数が一致しません")
                timestamp = datetime.datetime.fromisoformat(row["time"])
                if timestamp.tzinfo is None or row["run_status"] not in {"success", "partial"}:
                    raise ValueError("時刻・実行状態が不正です")
                if row["status"] not in {"success", "partial", "excluded", "missing", "unavailable"}:
                    raise ValueError("取得状態が不正です")
                json.loads(row["conditions"])
                size = int(row["size"]) if row["size"] else None
                if (size is not None and size < 0) or (row["status"] == "success" and size is None):
                    raise ValueError("サイズが不正です")
                if row["target"] == target and row["conditions"] == conditions and row["run_status"] == "success":
                    if last_time != row["time"]:
                        previous, last_time = {}, row["time"]
                    if size is not None and row["status"] == "success":
                        previous[row["path"]] = size
    except (ValueError, csv.Error, UnicodeError) as exc:
        raise ValueError(f"CSV履歴が不正です: {path}: {exc}。履歴を別名へ退避して再実行してください") from exc
    return previous


def _uv_candidate(target: pathlib.Path, nodes: list[_Node]) -> tuple[dict[str, str] | None, str]:
    command = ["uv", "cache", "dir", "--no-config"]
    try:
        with process_scope(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
        ) as process:
            stdout, stderr = process.communicate(timeout=15)
            result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except OSError as exc:
        return None, (
            f"uvキャッシュの場所を取得できません: {exc}。"
            "uv --version で起動を確認し、uvの導入状態・PATH・実行権限を確認してください。"
            "uv cache dir --no-config が成功した後、同じdirsizeコマンドでレポートを再生成してください。"
        )
    except subprocess.TimeoutExpired as exc:
        return None, (
            f"uvキャッシュの場所の照会が時間内に完了しませんでした: {exc}。"
            "uv cache dir --no-config を端末で実行し、停止やアクセスエラーの原因を確認してください。"
            "照会が成功した後、同じdirsizeコマンドでレポートを再生成してください。"
        )
    if result.returncode:
        return None, (
            f"uvキャッシュの場所を取得できません: {format_cli_error(result)}。"
            "uv cache dir --no-config を端末で実行し、表示された診断に従って設定やアクセス状態を確認してください。"
            "照会が成功した後、同じdirsizeコマンドでレポートを再生成してください。"
        )
    # CLI出力の終端だけを除き、POSIXパスに含まれる末尾空白・改行は保持する。
    root = pathlib.Path(result.stdout.removesuffix("\n"))
    if not root.is_absolute():
        return None, (
            "uvが絶対パスを返しませんでした。uv cache dir --no-config を端末で実行し、"
            "UV_CACHE_DIRを設定している場合は絶対パスに直してください。"
            "絶対パスが返る状態になった後、同じdirsizeコマンドでレポートを再生成してください。"
        )
    # resolveでリンクされた個別ディレクトリを別の完全取得ルートへ変換しない。
    if not root.is_relative_to(target):
        return None, "uvキャッシュは走査対象外です"
    branch = [node for node in nodes if pathlib.Path(node.path).is_relative_to(root)]
    if not branch or branch[0].path != str(root) or branch[0].kind != "directory":
        if any(node.status == "excluded" and root.is_relative_to(pathlib.Path(node.path)) for node in nodes):
            return None, "uvキャッシュルートは走査から除外されています"
        return None, (
            "uvキャッシュルートをディレクトリとして取得できませんでした。"
            "uv cache dir --no-config を端末で実行し、表示されたルートの存在と読み取り権限を確認してください。"
            "アクセス可能な状態になった後、同じdirsizeコマンドでレポートを再生成してください。"
        )
    if any(node.status in {"missing", "unavailable", "partial"} for node in branch):
        return None, (
            "uvキャッシュルートを完全取得できませんでした。一覧を「除外・未取得」に切り替え、"
            "取得不能・消失パスの読み取り権限と存在を確認してください。"
            "アクセス可能な状態になった後、同じdirsizeコマンドでレポートを再生成してください。"
        )
    if any(node.status != "success" for node in branch):
        return None, "uvキャッシュルートの配下に除外されたパスがあるため、ルート全体のスクリプトは生成できません"
    args = ["uv", "cache", "prune", "--no-config", "--offline", "--cache-dir", str(root)]
    shell = "#!/bin/sh\nset -eu\n" + shlex.join(args) + "\n"
    powershell = "$ErrorActionPreference = 'Stop'\r\n& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in args)
    powershell += "\r\nif ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\r\n"
    return {"path": str(root), "shell": shell, "powershell": powershell}, ""


def _save(path: pathlib.Path, content: bytes) -> None:
    temporary: pathlib.Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}-", delete=False) as stream:
            temporary = pathlib.Path(stream.name)
            stream.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
