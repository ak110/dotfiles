"""pytools._internal.setup_statusline_binary のテスト。"""

import collections.abc
import subprocess
import typing
from pathlib import Path

import httpx
import pytest

from pytools._internal import setup_statusline_binary as mod


def _client(handler) -> httpx.Client:
    """MockTransportで擬似応答を返すHTTPクライアントを生成する。"""
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _without_working_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    """各テストの作業ツリー選択を明示的な入力だけに限定する。"""
    monkeypatch.delenv("CHEZMOI_WORKING_TREE", raising=False)


def _git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """一時Gitリポジトリへコマンドを実行する。"""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    )


def _make_git_repo(tmp_path: Path) -> Path:
    """origin/masterとdevelopが同じ初期コミットを指す一時Gitリポジトリを作成する。"""
    repo = tmp_path / "repo"
    _git(tmp_path, ["init", "--initial-branch=master", str(repo)])
    _git(repo, ["config", "user.email", "test@example.invalid"])
    _git(repo, ["config", "user.name", "test"])
    manifest = repo / "rust" / "claude-statusline" / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("[package]\nname = 'claude-statusline'\n", encoding="utf-8")
    _git(repo, ["add", "rust/claude-statusline/Cargo.toml"])
    _git(repo, ["commit", "-m", "initial"])
    _git(repo, ["switch", "-c", "develop"])
    _git(repo, ["update-ref", "refs/remotes/origin/master", "HEAD"])
    return repo


def _write_statusline_change(repo: Path) -> None:
    """statusline配下の追跡ファイルへ変更を加える。"""
    manifest = repo / "rust" / "claude-statusline" / "Cargo.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "description = 'changed'\n", encoding="utf-8")


def _commit_statusline_change(repo: Path) -> None:
    """statusline配下の変更をcommit済みにする。"""
    _write_statusline_change(repo)
    _git(repo, ["add", "rust/claude-statusline/Cargo.toml"])
    _git(repo, ["commit", "-m", "change"])


def _stage_statusline_change(repo: Path) -> None:
    """statusline配下の変更をstage済みにする。"""
    _write_statusline_change(repo)
    _git(repo, ["add", "rust/claude-statusline/Cargo.toml"])


def _prepare_install_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    """バイナリとETagの導入先を一時ディレクトリへ向ける。"""
    install_dir = tmp_path / "install"
    install_path = install_dir / ("claude-statusline.exe" if mod.sys.platform == "win32" else "claude-statusline")
    etag_path = install_dir / ".claude-statusline.etag"
    monkeypatch.setattr(mod, "_INSTALL_DIR", install_dir)
    monkeypatch.setattr(mod, "_INSTALL_PATH", install_path)
    monkeypatch.setattr(mod, "_ETAG_PATH", etag_path)
    return install_dir, install_path, etag_path


class _GitAndMiseStub:
    """Gitは実行し、miseのbuildだけを記録するスタブ。"""

    def __init__(self, *, build_returncode: int = 0, write_artifact: bool = True) -> None:
        self.build_returncode = build_returncode
        self.write_artifact = write_artifact
        self.build_calls: list[dict[str, typing.Any]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mod.claude_common, "run_subprocess", self.run)
        monkeypatch.setattr(mod.setup_mise, "find_mise_binary", lambda: Path("/fake/mise"))

    def run(self, command: list[str], **kwargs: typing.Any) -> subprocess.CompletedProcess[str]:
        if command[0] == "git":
            cwd = kwargs.get("cwd")
            assert isinstance(cwd, Path)
            return subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
        self.build_calls.append({"command": command, "kwargs": kwargs})
        cwd = kwargs.get("cwd")
        assert isinstance(cwd, Path)
        if self.write_artifact and self.build_returncode == 0:
            artifact = (
                cwd
                / "rust"
                / "claude-statusline"
                / "target"
                / "release"
                / ("claude-statusline.exe" if mod.sys.platform == "win32" else "claude-statusline")
            )
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"LOCAL")
        return subprocess.CompletedProcess(command, self.build_returncode, "", "build failed")


def _assert_local_build(stub: _GitAndMiseStub, repo: Path) -> None:
    """mise経由のローカルビルド呼び出しを検証する。"""
    assert len(stub.build_calls) == 1
    record = stub.build_calls[0]
    assert record["command"] == [
        "/fake/mise",
        "exec",
        "--",
        "cargo",
        "build",
        "--release",
        "--locked",
        "--manifest-path",
        "rust/claude-statusline/Cargo.toml",
    ]
    assert record["kwargs"]["cwd"] == repo


_TRACKED_CHANGE_PREPARERS: tuple[tuple[str, collections.abc.Callable[[Path], None]], ...] = (
    ("committed", _commit_statusline_change),
    ("staged", _stage_statusline_change),
    ("unstaged", _write_statusline_change),
)


@pytest.mark.parametrize(
    ("_state", "prepare_change"),
    _TRACKED_CHANGE_PREPARERS,
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_develop_builds_for_all_tracked_change_states(
    _state: str,
    prepare_change: collections.abc.Callable[[Path], None],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """developのcommit済み・staged・unstaged差分をローカルビルドへ接続する。"""
    del _state
    repo = _make_git_repo(tmp_path)
    prepare_change(repo)
    _, install_path, _ = _prepare_install_paths(monkeypatch, tmp_path)
    install_path.parent.mkdir(parents=True)
    install_path.write_bytes(b"OLD")
    monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(repo))
    stub = _GitAndMiseStub()
    stub.install(monkeypatch)

    assert mod.run() is True

    assert install_path.read_bytes() == b"LOCAL"
    _assert_local_build(stub, repo)


def test_develop_builds_for_nonignored_untracked_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """developのignore対象外untracked差分をローカルビルドへ接続する。"""
    repo = _make_git_repo(tmp_path)
    untracked = repo / "rust" / "claude-statusline" / "local-config.toml"
    untracked.write_text("local\n", encoding="utf-8")
    _, install_path, _ = _prepare_install_paths(monkeypatch, tmp_path)
    stub = _GitAndMiseStub()
    stub.install(monkeypatch)
    monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(repo))

    assert mod.run() is True

    assert install_path.read_bytes() == b"LOCAL"
    _assert_local_build(stub, repo)


def test_develop_without_statusline_diff_uses_release_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """developでもstatusline差分が無ければRelease downloadを使う。"""
    repo = _make_git_repo(tmp_path)
    _, install_path, _ = _prepare_install_paths(monkeypatch, tmp_path)
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, content=b"RELEASE")

    monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(repo))
    monkeypatch.setattr(mod.setup_mise, "find_mise_binary", lambda: pytest.fail("miseを呼び出している"))

    assert mod.run(client=_client(handler)) is True

    assert install_path.read_bytes() == b"RELEASE"
    assert requested_urls


def test_ignored_untracked_file_uses_release_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ignore対象のuntrackedファイルだけではRelease downloadを維持する。"""
    repo = _make_git_repo(tmp_path)
    gitignore = repo / ".gitignore"
    gitignore.write_text("rust/claude-statusline/ignored.toml\n", encoding="utf-8")
    _git(repo, ["add", ".gitignore"])
    _git(repo, ["commit", "-m", "ignore"])
    (repo / "rust" / "claude-statusline" / "ignored.toml").write_text("ignored\n", encoding="utf-8")
    _, install_path, _ = _prepare_install_paths(monkeypatch, tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"RELEASE")

    monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(repo))
    monkeypatch.setattr(mod.setup_mise, "find_mise_binary", lambda: pytest.fail("miseを呼び出している"))

    assert mod.run(client=_client(handler)) is True
    assert install_path.read_bytes() == b"RELEASE"


def test_non_develop_branch_uses_release_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """develop以外のbranchではRelease downloadを使う。"""
    repo = _make_git_repo(tmp_path)
    _git(repo, ["switch", "master"])
    _, install_path, _ = _prepare_install_paths(monkeypatch, tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"RELEASE")

    monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(repo))
    monkeypatch.setattr(mod.setup_mise, "find_mise_binary", lambda: pytest.fail("miseを呼び出している"))

    assert mod.run(client=_client(handler)) is True
    assert install_path.read_bytes() == b"RELEASE"


def test_non_git_working_tree_uses_release_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Git管理領域でない作業ツリーではRelease downloadを使う。"""
    working_tree = tmp_path / "not-repository"
    working_tree.mkdir()
    _, install_path, _ = _prepare_install_paths(monkeypatch, tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"RELEASE")

    monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(working_tree))
    monkeypatch.setattr(mod.setup_mise, "find_mise_binary", lambda: pytest.fail("miseを呼び出している"))

    assert mod.run(client=_client(handler)) is True
    assert install_path.read_bytes() == b"RELEASE"


def test_missing_origin_master_raises_without_release_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """developでorigin/masterを解決できない場合はReleaseへ切り替えず失敗する。"""
    repo = _make_git_repo(tmp_path)
    _git(repo, ["update-ref", "-d", "refs/remotes/origin/master"])
    _, install_path, _ = _prepare_install_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(repo))

    with pytest.raises(RuntimeError, match="origin/master"):
        mod.run()

    assert not install_path.exists()


def test_build_failure_preserves_existing_binary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ローカルビルド失敗時は既存バイナリを保持する。"""
    repo = _make_git_repo(tmp_path)
    _write_statusline_change(repo)
    _, install_path, etag_path = _prepare_install_paths(monkeypatch, tmp_path)
    install_path.parent.mkdir(parents=True)
    install_path.write_bytes(b"OLD")
    etag_path.write_text('"release"', encoding="utf-8")
    monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(repo))
    stub = _GitAndMiseStub(build_returncode=1)
    stub.install(monkeypatch)

    with pytest.raises(RuntimeError, match="ビルド"):
        mod.run()

    assert install_path.read_bytes() == b"OLD"
    assert etag_path.read_text(encoding="utf-8") == '"release"'


def test_missing_build_artifact_preserves_existing_binary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ビルド成果物の読取失敗時は既存バイナリを保持する。"""
    repo = _make_git_repo(tmp_path)
    _write_statusline_change(repo)
    _, install_path, _ = _prepare_install_paths(monkeypatch, tmp_path)
    install_path.parent.mkdir(parents=True)
    install_path.write_bytes(b"OLD")
    monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(repo))
    stub = _GitAndMiseStub(write_artifact=False)
    stub.install(monkeypatch)

    with pytest.raises(FileNotFoundError):
        mod.run()

    assert install_path.read_bytes() == b"OLD"


def test_atomic_replace_failure_preserves_existing_binary_and_invalidates_etag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """原子的配置が失敗しても既存バイナリを保持し、ETagを無効化する。"""
    repo = _make_git_repo(tmp_path)
    _write_statusline_change(repo)
    _, install_path, etag_path = _prepare_install_paths(monkeypatch, tmp_path)
    install_path.parent.mkdir(parents=True)
    install_path.write_bytes(b"OLD")
    etag_path.write_text('"release"', encoding="utf-8")
    monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(repo))
    stub = _GitAndMiseStub()
    stub.install(monkeypatch)

    def fail_atomic_write(_path: Path, _content: bytes, **_kwargs: typing.Any) -> bool:
        return False

    monkeypatch.setattr(mod.claude_common, "atomic_write_bytes", fail_atomic_write)

    with pytest.raises(RuntimeError, match="配置"):
        mod.run()

    assert install_path.read_bytes() == b"OLD"
    assert not etag_path.exists()


class TestRun:
    """`run()`のダウンロード・冪等スキップ・失敗時フォールバックを検証する。"""

    def test_fresh_install_writes_binary_and_etag(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        install_dir = tmp_path / "bin"
        monkeypatch.setattr(mod, "_INSTALL_DIR", install_dir)
        monkeypatch.setattr(mod, "_INSTALL_PATH", install_dir / "claude-statusline")
        monkeypatch.setattr(mod, "_ETAG_PATH", install_dir / ".claude-statusline.etag")

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"BINARY", headers={"etag": '"abc123"'})

        assert mod.run(client=_client(handler)) is True
        assert (install_dir / "claude-statusline").read_bytes() == b"BINARY"
        assert (install_dir / ".claude-statusline.etag").read_text(encoding="utf-8") == '"abc123"'

    def test_matching_etag_returns_304_and_skips_write(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        install_dir = tmp_path / "bin"
        install_dir.mkdir()
        binary_path = install_dir / "claude-statusline"
        binary_path.write_bytes(b"OLD")
        etag_path = install_dir / ".claude-statusline.etag"
        etag_path.write_text('"abc123"', encoding="utf-8")
        monkeypatch.setattr(mod, "_INSTALL_DIR", install_dir)
        monkeypatch.setattr(mod, "_INSTALL_PATH", binary_path)
        monkeypatch.setattr(mod, "_ETAG_PATH", etag_path)

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("if-none-match") == '"abc123"'
            return httpx.Response(304)

        assert mod.run(client=_client(handler)) is False
        assert binary_path.read_bytes() == b"OLD"

    def test_network_failure_returns_false_without_raising(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        install_dir = tmp_path / "bin"
        monkeypatch.setattr(mod, "_INSTALL_DIR", install_dir)
        monkeypatch.setattr(mod, "_INSTALL_PATH", install_dir / "claude-statusline")
        monkeypatch.setattr(mod, "_ETAG_PATH", install_dir / ".claude-statusline.etag")

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        assert mod.run(client=_client(handler)) is False


class TestDownloadUrlOverride:
    """`DOTFILES_STATUSLINE_DOWNLOAD_URL`によるダウンロードURLオーバーライドを検証する。"""

    def test_override_url_is_used_when_set(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        install_dir = tmp_path / "bin"
        monkeypatch.setattr(mod, "_INSTALL_DIR", install_dir)
        monkeypatch.setattr(mod, "_INSTALL_PATH", install_dir / "claude-statusline")
        monkeypatch.setattr(mod, "_ETAG_PATH", install_dir / ".claude-statusline.etag")
        monkeypatch.setenv("DOTFILES_STATUSLINE_DOWNLOAD_URL", "http://127.0.0.1:9/override")
        requested_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_urls.append(str(request.url))
            return httpx.Response(200, content=b"BINARY")

        assert mod.run(client=_client(handler)) is True
        assert requested_urls == ["http://127.0.0.1:9/override"]
