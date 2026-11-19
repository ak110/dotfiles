"""配布物へ書き込まず、CLI・hook・MCPが同じ版の依存環境を使う起動条件を解決する。

依存導入前にscripts/launch.pyが使うため、標準ライブラリだけに依存する。
環境の指定はuv子プロセスに限り、Bashや対象リポジトリのuvへ永続化しない。
実行依存は`DEPENDENCY_SCRIPT`のインラインメタデータ（PEP 723）が宣言し、隣の`.lock`が固定する。
"""

import errno
import hashlib
import json
import os
import platform
import shlex
import sys
import time
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO
lazy import logging

from agent_toolkit._common import inherited_venv
lazy from agent_toolkit._common.shell_tokens import atk_launcher_name

RUNTIME_DATA_ENV = "AGENT_TOOLKIT_RUNTIME_DATA"
RUNTIME_ENVIRONMENT_ENV = inherited_venv.PLUGIN_RUNTIME_ENVIRONMENT_KEY
# 実行依存を宣言するスクリプトの配布root相対パス。uvの`--script`で同期し、固定内容は同名に`.lock`を付けたファイルに置く。
DEPENDENCY_SCRIPT = Path("scripts/launch.py")
_READY_FILE = ".agent-toolkit-dependencies-ready"
_PREPARE_LOCK_TIMEOUT = 300.0
_PREPARE_LOCK_INTERVAL = 0.1


def runtime_data(environment: Mapping[str, str]) -> Path:
    """明示指定を優先し、次にOSのユーザーデータ領域、書き込めない場合はホストのplugin dataを使う。

    端末・Claude Code・Codexと導入後のウォームアップが同じ版の環境を共用できるよう、
    ホストごとに異なるplugin dataはユーザーデータ領域へ書き込めない場合だけ使う。
    """
    if value := environment.get(RUNTIME_DATA_ENV):
        return Path(value).expanduser().resolve()
    home = Path(environment.get("USERPROFILE" if sys.platform == "win32" else "HOME") or Path.home())
    if sys.platform == "win32":
        base = Path(environment.get("LOCALAPPDATA", str(home / "AppData/Local")))
    elif sys.platform == "darwin":
        base = home / "Library/Application Support"
    else:
        base = Path(environment.get("XDG_DATA_HOME", str(home / ".local/share")))
    candidates = [base / "agent-toolkit"]
    candidates.extend(Path(value) for name in ("CLAUDE_PLUGIN_DATA", "PLUGIN_DATA") if (value := environment.get(name)))
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if _writable_directory(resolved):
            return resolved
    return candidates[0].expanduser().resolve()


def _writable_directory(path: Path) -> bool:
    """ディレクトリを作成でき、書き込み可能なら真を返す。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(path, os.W_OK)


def base_python(python_executable: Path | None = None) -> Path:
    """仮想環境の実行ファイルを、環境を作ったPython本体へ正規化する。

    シンボリックリンクで作られた仮想環境は、リンクの解決だけで本体へ届く。
    解決後も仮想環境の中に残る実行ファイル（複製で作られた環境）だけ、`pyvenv.cfg`から本体を探す。
    `home`には別の版を指す`python`が置かれることがあるため、版を含む名前を同名より先に探す。
    """
    executable = python_executable or Path(sys.executable)
    seen: set[Path] = set()
    while True:
        resolved = executable.resolve()
        seen.add(resolved)
        config = executable.parent.parent / "pyvenv.cfg"
        if not config.is_file() or not resolved.is_relative_to(config.parent.resolve()):
            return resolved
        settings: dict[str, str] = {}
        for line in config.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                settings.setdefault(key.strip(), value.strip())
        # 標準の`venv`は`executable`と`version`、uvは`version_info`を書く。
        # Windowsでvenvから作ると`executable`もvenvを指すため、本体まで解決を続ける。
        candidates = [Path(settings["executable"])] if settings.get("executable") else []
        if home := settings.get("home"):
            version = (settings.get("version_info") or settings.get("version") or "").split(".")
            if len(version) >= 2:
                candidates.append(Path(home) / f"python{version[0]}.{version[1]}{executable.suffix}")
            candidates.append(Path(home) / executable.name)
        candidate = next((path for path in candidates if path.is_file() and path.resolve() not in seen), None)
        if candidate is None:
            return resolved
        executable = candidate


def dependency_lock(root: Path) -> Path:
    """配布rootの実行依存の固定内容（uvがスクリプトの隣に置くlockfile）を返す。"""
    script = root / DEPENDENCY_SCRIPT
    return script.with_name(script.name + ".lock")


def project_environment(root: Path, environment: Mapping[str, str], *, python_executable: Path | None = None) -> Path:
    """依存解決とPython実行条件が一致する実行だけが共有する環境を返す。

    lockfileは依存宣言（manifest）と解決オプションも記録し、同期は宣言との一致を`--locked`で確かめるため、
    lockfileの内容だけで依存構成を識別する。
    """
    root = root.resolve()
    payload = {
        "lock": hashlib.sha256(dependency_lock(root).read_bytes()).hexdigest(),
        # 作業ディレクトリの仮想環境と共有環境自身は、同じPython本体の別パスとして現れる。
        # 呼び出し元の文脈によらず同じ共有環境を得るため、Python本体だけで識別する。
        "python": str(base_python(python_executable)),
        "platform": {"os": sys.platform, "machine": platform.machine()},
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded)
    return runtime_data(environment) / "environments" / digest.hexdigest()


def launch_environment(root: Path, environment: Mapping[str, str]) -> dict[str, str]:
    """準備・直接起動の子プロセス環境を組み立てる。"""
    result = dict(environment)
    inherited_venv.strip_inherited_venv(result)
    caller = dict(result)
    if "UV_PROJECT_ENVIRONMENT" in result:
        result[inherited_venv.CALLER_PROJECT_ENVIRONMENT_KEY] = result["UV_PROJECT_ENVIRONMENT"]
    selected = str(project_environment(root, environment))
    result[RUNTIME_DATA_ENV] = str(runtime_data(environment))
    result[RUNTIME_ENVIRONMENT_ENV] = selected
    result["UV_PROJECT_ENVIRONMENT"] = selected
    inherited_venv.record_launch_changes(result, caller)
    return result


def python_path(environment: Path) -> Path:
    """共有依存環境のPython実行ファイルを返す。"""
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def prepared_python(root: Path, environment: Mapping[str, str]) -> Path | None:
    """完了を公開済みの共有環境のPythonだけを返す。"""
    selected = project_environment(root, environment)
    executable = python_path(selected)
    if (selected / _READY_FILE).is_file() and executable.is_file():
        return executable
    return None


def direct_environment(root: Path, environment: Mapping[str, str]) -> dict[str, str]:
    """rootのコードを共有依存環境のPythonで解決する環境を返す。"""
    result = launch_environment(root, environment)
    caller = dict(result)
    inherited_venv.strip_inherited_venv(caller)
    root_text = str(root.resolve())
    previous = result.get("PYTHONPATH")
    result["PYTHONPATH"] = root_text + (os.pathsep + previous if previous else "")
    inherited_venv.record_launch_changes(result, caller)
    return result


@contextmanager
def prepare_lock(environment: Path) -> Generator[None]:
    """OSファイルロックで同一共有環境のprepareを直列化する。"""
    lock = environment.with_name(environment.name + ".prepare-lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as stream:
        _lock_file(stream, lock)
        try:
            yield
        finally:
            _unlock_file(stream)


def _lock_file(stream: BinaryIO, lock: Path) -> None:
    """既知の競合だけを有限時間待機して排他ロックを取得する。"""
    deadline = time.monotonic() + _PREPARE_LOCK_TIMEOUT
    stream.seek(0)
    if os.name == "nt":
        import msvcrt  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,import-error

        while True:
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
                return
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EDEADLK}:
                    raise
                _wait_for_prepare_lock(lock, deadline, error)
    import fcntl  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,import-error

    while True:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError as error:
            _wait_for_prepare_lock(lock, deadline, error)


def _wait_for_prepare_lock(lock: Path, deadline: float, error: OSError) -> None:
    """ロック待機の上限を一箇所で判定し、競合以外を隠さない。"""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(
            f"共有依存環境のprepare lockが{_PREPARE_LOCK_TIMEOUT:.0f}秒以内に解放されませんでした: {lock}"
        ) from error
    time.sleep(min(_PREPARE_LOCK_INTERVAL, remaining))


def _unlock_file(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,import-error

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        return
    import fcntl  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,import-error

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def publish_prepared_environment(root: Path, environment: Mapping[str, str]) -> Path:
    """最終パスの同期完了をready markerの原子的配置で公開する。"""
    target = project_environment(root, environment)
    temporary = target / f"{_READY_FILE}.tmp-{os.getpid()}"
    temporary.write_text("ready\n", encoding="utf-8")
    temporary.replace(target / _READY_FILE)
    record_source(root, target)
    return target


def record_source(root: Path, target: Path) -> None:
    """prepareの排他下で、同じ実体を共有する配布rootと版の対応を残す。"""
    try:
        _record_source(root, target)
    except (OSError, ValueError, TypeError) as error:
        logging.getLogger(__name__).warning(
            "共有環境の版対応を保存できませんでした: %s。%s と %s の内容・読み書き権限を確認してください。"
            "壊れた版対応JSONは退避して削除し、配布manifestの破損はプラグインの再インストールで復元してください。"
            "原因を解消してから元のプラグイン準備操作を再実行してください。起動可能な環境は保持しています",
            error,
            target / ".agent-toolkit-sources.json",
            root / ".claude-plugin/plugin.json",
        )


def _record_source(root: Path, target: Path) -> None:
    """破損した対応情報は上書きせず、準備完了とも切り離して扱う。"""
    path = target / ".agent-toolkit-sources.json"
    try:
        sources = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sources = {}
    if not isinstance(sources, dict):
        raise ValueError(f"版対応がobjectではありません: {path}")
    try:
        manifest = json.loads((root / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        manifest = {}
    if not isinstance(manifest, dict):
        raise ValueError(f"plugin manifestがobjectではありません: {root}")
    sources[str(root.resolve())] = manifest.get("version")
    temporary = path.with_suffix(f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(sources, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def launch_command(root: Path, target: Path) -> list[str]:
    """配布root内のスクリプトを、配布root外の依存環境で起動するargvを返す。

    配布rootをuvのprojectに指定するとそこへ仮想環境が作成されるため、案内文もlaunch.pyを使う。
    """
    launcher = root.resolve() / "scripts" / "launch.py"
    return ["uv", "run", "--no-cache", "--no-config", "--no-project", "--python", "3.15", "python", str(launcher), str(target)]


def cli_environment(root: Path, environment: Mapping[str, str]) -> dict[str, str]:
    """委譲先が保持したrootのCLIを名前で呼べるよう、PATHとデータ領域の環境変数を加えた環境を返す。"""
    result = dict(environment)
    scripts = str(root.resolve() / "scripts")
    path = result.get("PATH", "")
    if scripts not in path.split(os.pathsep):
        result["PATH"] = scripts + (os.pathsep + path if path else "")
    result[RUNTIME_DATA_ENV] = str(runtime_data(environment))
    return result


def persist_claude_environment(root: Path, environment: Mapping[str, str]) -> None:
    """提供されたBash環境ファイルへ追記し、別hookの設定と後続のPATHを保持する。"""
    if not (value := environment.get("CLAUDE_ENV_FILE")):
        return
    scripts = shlex.quote(str(root.resolve() / "scripts"))
    if os.name == "nt":
        # BashのPATHではドライブのコロンも区切りになるため、Git Bashの表記へ変換する。
        scripts = f'"$(cygpath -u {scripts})"'
    data = shlex.quote(str(runtime_data(environment)))
    with Path(value).open("a", encoding="utf-8", newline="\n") as output:
        output.write(f'\nexport PATH={scripts}:"$PATH"\nexport {RUNTIME_DATA_ENV}={data}\n')


def cli_instructions(root: Path, *, windows: bool | None = None) -> str:
    """直接起動と委譲の文脈へ、同じrootのCLIとホストに合う呼び方を示す。"""
    is_windows = os.name == "nt" if windows is None else windows
    entry = root / "scripts" / atk_launcher_name(windows=is_windows)
    command = "& '" + str(entry).replace("'", "''") + "'" if is_windows else shlex.quote(str(entry))
    return f"起動部分: {command}\nこの起動部分へサブコマンドと引数を続けよ。CLIはこの配布物と、その版の依存環境を使う。"
