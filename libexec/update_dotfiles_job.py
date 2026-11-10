"""uvの外側で更新workerを分離し、同じjobの出力と終了状態を回収する。"""

# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///

import codecs
import contextlib
import ctypes
import datetime
import io
import json
import os
import pathlib
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, BinaryIO, cast

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "agent-toolkit"))

from agent_toolkit._common.windows_job import CREATE_SUSPENDED, WindowsJob  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_TIMEOUT = 7200
_RETENTION = 7 * 24 * 60 * 60
_WARNING = (
    "uvの自己更新に失敗しました。既存のuvでdotfiles更新を実行し、次回のupdate-dotfiles起動時に自己更新を再試行します。"
    "対応不要（dotfilesの更新処理は継続しました）。"
)


def state_root(platform: str = sys.platform) -> pathlib.Path:
    """platformdirsと同じOS既定の状態領域を、外部依存なしで解決する。"""
    if platform == "win32":
        base = pathlib.Path(os.environ.get("WIN_PD_OVERRIDE_LOCAL_APPDATA") or os.environ["LOCALAPPDATA"])
        return base / "agent-toolkit" / "update-jobs"
    if platform == "darwin":
        return pathlib.Path.home() / "Library" / "Application Support" / "agent-toolkit" / "update-jobs"
    return (
        pathlib.Path(os.environ.get("XDG_STATE_HOME", str(pathlib.Path.home() / ".local/state"))) / "agent-toolkit/update-jobs"
    )


def separation_options(platform: str = sys.platform) -> dict[str, Any]:
    """起動元のセッションまたはWindows Job Objectからworkerを分離する。"""
    if platform == "win32":
        # Win32 CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB。
        # DETACHED_PROCESSはコンソールを持たせず、workerが起動するコンソールアプリへ可視ウィンドウを割り当てさせる。
        # CREATE_NO_WINDOWの非表示コンソールは起動元の端末と独立し、子孫も同じ非表示コンソールを継承する。
        return {"creationflags": 0x00000200 | 0x08000000 | 0x01000000}
    return {"start_new_session": True}


def _job_path(identifier: str) -> pathlib.Path:
    if str(uuid.UUID(identifier)) != identifier:
        raise ValueError("job IDは正規のUUIDで指定してください")
    root = state_root().resolve()
    path = root / identifier
    if path.resolve().parent != root or path.is_symlink():
        raise ValueError("jobの保存先が所有する状態領域の外を参照しています")
    return path


def _write_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _cleanup() -> None:
    root = state_root()
    if not root.exists():
        return
    for path in root.iterdir():
        if path.is_symlink() or not path.is_dir():
            continue
        try:
            owned = _job_path(path.name)
            result = json.loads((owned / "result.json").read_text(encoding="utf-8"))
            finished = datetime.datetime.fromisoformat(result["finished_at"]).timestamp()
        except ValueError, KeyError, TypeError, FileNotFoundError:
            continue
        if time.time() - finished > _RETENTION:
            shutil.rmtree(owned)


def _procfs_parents() -> list[tuple[int, int]]:
    """Linuxの全プロセスの`(pid, ppid)`をprocfsから返す。

    最小構成のコンテナーなどprocpsの無い環境でも子孫を停止できるよう、`ps`を使わない。
    """
    pairs: list[tuple[int, int]] = []
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError, ProcessLookupError:
            # 列挙中に終了したプロセスは停止対象から外れてよい。
            continue
        # commは空白や括弧を含み得るため、最後の`)`より後ろの`state ppid ...`を読む。
        pairs.append((int(entry.name), int(stat.rpartition(")")[2].split()[1])))
    return pairs


def _stop_tree(process: subprocess.Popen[Any], windows_job: WindowsJob | None = None) -> None:
    try:
        if sys.platform == "linux":
            deadline = time.monotonic() + 30
            while True:
                # 直系の子はこのworkerがwaitするまでPIDを再利用されない。
                # 親の終了でsubreaperへ移った孫も、次の列挙で同じ所有条件になる。
                for pid, parent in _procfs_parents():
                    if parent == os.getpid():
                        with contextlib.suppress(ProcessLookupError):
                            os.kill(pid, signal.SIGKILL)
                process.wait(timeout=30)
                try:
                    while os.waitpid(-1, os.WNOHANG)[0] != 0:
                        pass
                except ChildProcessError:
                    return
                if time.monotonic() >= deadline:
                    raise RuntimeError("更新の子孫プロセスの終了確認が未完了です")
                time.sleep(0.1)
        if sys.platform == "win32":
            assert windows_job is not None
            windows_job.terminate(timeout=30)
        else:
            result = subprocess.run(
                ["ps", "-e", "-o", "pid=,ppid="],
                check=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            parents = [tuple(map(int, line.split())) for line in result.stdout.splitlines()]
            descendants = {os.getpid()}
            while children := {pid for pid, parent in parents if parent in descendants} - descendants:
                descendants.update(children)
            descendants.discard(os.getpid())
            for pid in reversed(sorted(descendants)):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
            # 回収済みの親の数値PGIDは、別の群へ再利用され得る。
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=30)
    finally:
        # 列挙・子孫回収の診断時にも、Popenの無期限waitへ進めない。
        if process.poll() is None:
            if sys.platform != "win32":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            process.kill()
            process.wait(timeout=30)


def _forward_output(
    source: BinaryIO,
    destination: BinaryIO,
    errors: queue.SimpleQueue[Exception],
    forwarding: threading.Event,
    output_lock: threading.Lock,
) -> None:
    try:
        while data := source.read(65536):
            with output_lock:
                if not forwarding.is_set():
                    return
                destination.write(data)
                destination.flush()
    except Exception as error:  # threadの失敗をworkerの結果へ伝える境界。
        errors.put(error)


def _run_command(command: list[str], job: pathlib.Path, deadline: float, env: dict[str, str]) -> int:
    with contextlib.ExitStack() as stack:
        # 正常完了はpipeのEOFで判定する。出力を閉じたサービスはJob解放後も存続できる。
        windows_job = stack.enter_context(WindowsJob(kill_on_close=False)) if sys.platform == "win32" else None
        options = separation_options()
        if windows_job is not None:
            options["creationflags"] |= CREATE_SUSPENDED
        process = stack.enter_context(
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env=env,
                **options,
            )
        )
        readers: list[threading.Thread] = []
        errors: queue.SimpleQueue[Exception] = queue.SimpleQueue()
        forwarding = threading.Event()
        forwarding.set()
        output_lock = threading.Lock()
        completed = False
        try:
            if windows_job is not None:
                handle = int(cast(Any, process)._handle)  # noqa: SLF001  # pylint: disable=protected-access
                windows_job.assign_suspended(handle, process.pid)
            assert process.stdout is not None and process.stderr is not None
            for source, destination in ((process.stdout, sys.stdout), (process.stderr, sys.stderr)):
                reader = threading.Thread(
                    target=_forward_output, args=(source, destination.buffer, errors, forwarding, output_lock), daemon=True
                )
                reader.start()
                readers.append(reader)
            while True:
                exited = process.poll() is not None
                output_finished = all(not reader.is_alive() for reader in readers)
                if not errors.empty():
                    raise RuntimeError(f"更新出力の回収に失敗しました: {errors.get()}")
                if exited and output_finished:
                    completed = True
                    return process.returncode
                cancelled = (job / "stop").exists()
                if cancelled or time.monotonic() >= deadline:
                    reason = "更新は中断され未完了です。" if cancelled else f"更新は{_TIMEOUT}秒の上限を超え未完了です。"
                    print(
                        f"{reason}"
                        f"{job / 'stdout.log'}と{job / 'stderr.log'}を確認し、"
                        "原因を解消してupdate-dotfilesを再実行してください。",
                        file=sys.stderr,
                        flush=True,
                    )
                    return 130 if cancelled else 124
                time.sleep(0.1)
        finally:
            try:
                if not completed:
                    _stop_tree(process, windows_job)
            finally:
                try:
                    output_deadline = time.monotonic() + 30
                    for reader in readers:
                        reader.join(timeout=max(0, output_deadline - time.monotonic()))
                    if any(reader.is_alive() for reader in readers):
                        raise RuntimeError("更新出力の読取threadの終了確認が未完了です")
                finally:
                    # 停止失敗で読取が残っても、失敗結果公開後のログへの転送を止める。
                    # raw pipeはBufferedReaderの読取lockを持たず、終了時closeも待ち続けない。
                    with output_lock:
                        forwarding.clear()
            if not errors.empty():
                raise RuntimeError(f"更新出力の回収に失敗しました: {errors.get()}")


def _worker(job: pathlib.Path) -> int:
    metadata = json.loads((job / "input.json").read_text(encoding="utf-8"))
    arguments = metadata["arguments"]
    deadline = time.monotonic() + _TIMEOUT
    env = os.environ.copy()
    env["DOTFILES_PRUNE_OUTSIDE_UV"] = "1"
    env["PYTHONIOENCODING"] = "utf-8:replace"
    home_bin = pathlib.Path.home() / ".local/bin"
    uv = str(home_bin / ("uv.exe" if sys.platform == "win32" else "uv"))
    code = 1
    try:
        if sys.platform == "linux":
            # 直系の子を回収前に自動消滅させず、PIDの所有と終了コードを維持する。
            signal.signal(signal.SIGCHLD, signal.SIG_DFL)
            # uvや更新本体が終了した後の子孫も、独立workerが終了状態を回収する。
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
                raise OSError(ctypes.get_errno(), "更新workerの子孫所有を設定できません")
        update_failed = False
        if os.environ.get("AGENT_TOOLKIT_PROCESS_LOOP_SESSION") != "1":
            update = (
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(_ROOT / "libexec/update_uv.ps1"),
                    uv,
                ]
                if sys.platform == "win32"
                else ["bash", str(_ROOT / "libexec/update_uv.sh"), uv]
            )
            update_code = _run_command(update, job, deadline, env)
            if update_code in (124, 130):
                code = update_code
                return code
            update_failed = update_code != 0
            code = _run_command([uv, "python", "install", "--upgrade", "3.15"], job, deadline, env)
            if code:
                return code
        code = _run_command(
            [uv, "run", "--no-project", "--script", str(_ROOT / "libexec/update_dotfiles.py"), *arguments], job, deadline, env
        )
        if code == 0:
            code = _run_command(
                [
                    str(home_bin / ("dotfiles-post-apply.exe" if sys.platform == "win32" else "dotfiles-post-apply")),
                    "--prune-only-if-minimal",
                    *arguments,
                ],
                job,
                deadline,
                env,
            )
        if update_failed:
            print(_WARNING, file=sys.stderr, flush=True)
        return code
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        print(f"更新jobを完了できませんでした: {error}。保存出力を確認して再実行してください。", file=sys.stderr, flush=True)
        code = 1
        return code
    finally:
        # result公開前に、継承先を含む書込み済み出力をcontrollerから読める状態にする。
        sys.stdout.flush()
        sys.stderr.flush()
        _write_json(job / "result.json", {"exit_code": code, "finished_at": _now()})


def _retrieve(job: pathlib.Path, *, wait: bool, worker: subprocess.Popen[Any] | None = None) -> int:
    offsets = {"stdout": 0, "stderr": 0}
    decoders = {name: codecs.getincrementaldecoder("utf-8")(errors="replace") for name in offsets}
    interrupted = False
    while True:
        try:
            result_path = job / "result.json"
            finished = result_path.exists()
            for name, stream in (("stdout", sys.stdout), ("stderr", sys.stderr)):
                with (job / f"{name}.log").open("rb") as source:
                    source.seek(offsets[name])
                    data = source.read()
                    offsets[name] += len(data)
                stream.write(decoders[name].decode(data, final=finished))
                stream.flush()
            if finished:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                print(f"job {job.name}: 終了 exit={result['exit_code']}", flush=True)
                return result["exit_code"]
            if not wait:
                print(f"job {job.name}: 未完了。update-dotfiles job {job.name} で再取得してください。", flush=True)
                return 1
            # resultの公開とworker終了の競合では、次の反復で公開済み結果を優先する。
            if worker is not None and worker.poll() is not None and not result_path.exists():
                print(f"job {job.name}: workerが結果を保存せず終了しました。保存出力を確認してください。", file=sys.stderr)
                return 1
            time.sleep(0.1)
        except KeyboardInterrupt:
            if not interrupted:
                (job / "stop").touch()
                interrupted = True


def main(argv: list[str] | None = None) -> int:
    """新規job、保存結果の再取得、内部workerを実行する。"""
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if arguments[:1] == ["--worker"]:
            if len(arguments) != 2:
                raise ValueError("workerにはjob IDを1件指定してください")
            return _worker(_job_path(arguments[1]))
        if arguments[:1] == ["job"]:
            if len(arguments) > 2:
                raise ValueError("使い方: update-dotfiles job [UUID]")
            if len(arguments) == 2:
                job = _job_path(arguments[1])
            else:
                thread = os.environ.get("CODEX_THREAD_ID")
                if not thread:
                    raise ValueError("自己jobの取得にはCODEX_THREAD_IDが必要です。job IDを指定してください")
                matches = []
                for path in state_root().glob("*/input.json"):
                    metadata = json.loads(path.read_text(encoding="utf-8"))
                    if metadata.get("thread_id") == thread:
                        matches.append((metadata["started_at"], _job_path(path.parent.name)))
                if not matches:
                    raise ValueError("同じthreadの更新jobがありません")
                job = max(matches)[1]
            return _retrieve(job, wait=False)
        _cleanup()
        identifier = str(uuid.uuid4())
        job = _job_path(identifier)
        job.mkdir(parents=True, mode=0o700)
        _write_json(
            job / "input.json", {"arguments": arguments, "thread_id": os.environ.get("CODEX_THREAD_ID"), "started_at": _now()}
        )
        print(f"更新job: {identifier}\n保存先: {job}\n再取得: update-dotfiles job {identifier}", flush=True)
        with (job / "stdout.log").open("wb") as stdout, (job / "stderr.log").open("wb") as stderr:
            # workerは独立寿命を持つため、controllerでwait/terminateするcontext managerに入れない。
            try:
                worker = subprocess.Popen(  # noqa: S603  # pylint: disable=consider-using-with
                    [sys.executable, str(pathlib.Path(__file__).resolve()), "--worker", identifier],
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    close_fds=True,
                    **separation_options(),
                )
            except OSError as error:
                stderr.write(
                    f"更新workerの分離起動に失敗しました: {error}。保存先の出力と起動環境を確認してください。\n".encode()
                )
                stderr.flush()
                _write_json(job / "result.json", {"exit_code": 1, "finished_at": _now()})
                return _retrieve(job, wait=False)
        code = _retrieve(job, wait=True, worker=worker)
        worker.wait(timeout=30)
        return code
    except (OSError, ValueError, KeyError) as error:
        print(f"更新jobを取得・開始できません: {error}。表示された保存先とjob IDを確認してください。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    for output in (sys.stdout, sys.stderr):
        if isinstance(output, io.TextIOWrapper):
            output.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
