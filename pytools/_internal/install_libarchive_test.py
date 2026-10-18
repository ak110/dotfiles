"""install_libarchiveのパッケージ展開と、消費側と同じ解決条件による導入済み判定・再取得のテスト。"""

import compression.zstd
import io
import logging
import pathlib
import sys
import tarfile
import typing

import httpx

# 消費側の試験は実際のlibarchive-cでアーカイブを展開する。導入先の模擬DLLを`LIBARCHIVE`が指す前に読み込んでおく。
import libarchive  # noqa: F401  # pylint: disable=unused-import
import pytest

from pytools import repack_archive
from pytools._internal import install_libarchive, libarchive_resolver


def _compress(data: bytes) -> bytes:
    """標準ライブラリでzstd圧縮する。"""
    return compression.zstd.compress(data)


def _package(members: dict[str, bytes]) -> bytes:
    """MSYS2のpkg.tar.zstと同じ形式のバイト列を生成する。"""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:") as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return _compress(buffer.getvalue())


def test_extract_dlls_places_only_bin_dlls(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`bin/`配下のDLLだけを配置先へ保存する。"""
    monkeypatch.setattr(install_libarchive, "_INSTALL_DIR", tmp_path)
    package = _package(
        {
            "mingw64/bin/libarchive-13.dll": b"dll-body",
            "mingw64/lib/libarchive.dll.a": b"import-library",
            "mingw64/share/doc/readme.dll": b"not-in-bin",
        }
    )

    install_libarchive._extract_dlls(package)  # pylint: disable=protected-access

    assert sorted(path.name for path in tmp_path.iterdir()) == ["libarchive-13.dll"]
    assert (tmp_path / "libarchive-13.dll").read_bytes() == b"dll-body"


def test_decompress_zst_rejects_output_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """展開後のサイズが上限を超える入力は途中の内容を返さず失敗させる。"""
    monkeypatch.setattr(install_libarchive, "_MAX_DECOMPRESSED_SIZE", 16)

    with pytest.raises(ValueError, match="上限"):
        install_libarchive._decompress_zst(_compress(b"x" * 64))  # pylint: disable=protected-access


_REQUIRED = {"pkg-archive": "libarchive-13.dll", "pkg-zlib": "zlib1.dll"}


class _Repository:
    """MSYS2リポジトリの代わりにパッケージ一覧と各パッケージを返し、要求件数を数える。"""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.missing: set[str] = set()
        self.broken: set[str] = set()
        self.omitted_dlls: set[str] = set()
        self.unreachable: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        name = request.url.path.rsplit("/", 1)[-1]
        if not name:
            links = "".join(
                f'<a href="{prefix}-1.0-1-any.pkg.tar.zst">x</a>' for prefix in _REQUIRED if prefix not in self.missing
            )
            return httpx.Response(200, text=links)
        prefix = name.removesuffix("-1.0-1-any.pkg.tar.zst")
        if prefix in self.unreachable:
            raise httpx.ConnectError("接続できない", request=request)
        if prefix in self.broken:
            return httpx.Response(200, content=b"not-a-zstd-frame")
        dll = _REQUIRED[prefix]
        members = {} if dll in self.omitted_dlls else {f"mingw64/bin/{dll}": dll.encode()}
        return httpx.Response(200, content=_package(members))


@pytest.fixture(name="windows_env")
def windows_env_fixture(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[pathlib.Path, _Repository, list[str]]:
    """Windowsの導入先・DLL探索・HTTP・ユーザーPATHを隔離し、消費側と同じ解決条件で判定させる。

    ロードは配置先に本体と全依存DLLが揃う場合だけ成功する（`ctypes`のDLL loaderと同じ成立条件）。
    """
    install_dir = tmp_path / "libarchive"
    repository = _Repository()
    path_writes: list[str] = []
    monkeypatch.setattr(install_libarchive, "_INSTALL_DIR", install_dir)
    monkeypatch.setattr(install_libarchive, "_REQUIRED_PACKAGES", list(_REQUIRED))
    monkeypatch.setattr(install_libarchive, "_persist_libarchive_env_var", lambda: False)
    monkeypatch.setattr(install_libarchive.winutils, "append_user_path", path_writes.append)
    monkeypatch.setattr(libarchive_resolver.sys, "platform", "win32")
    monkeypatch.setattr(libarchive_resolver.os, "add_dll_directory", lambda _path: None, raising=False)
    monkeypatch.setattr(libarchive_resolver.ctypes.util, "find_library", lambda _name: None)
    # 導入処理はプロセス環境の`LIBARCHIVE`を設定するため、終了時に元の状態へ戻すよう記録してから外す。
    monkeypatch.setenv("LIBARCHIVE", "")
    monkeypatch.delenv("LIBARCHIVE")

    def load(path: str) -> None:
        directory = pathlib.Path(path).parent
        if not all((directory / dll).is_file() for dll in _REQUIRED.values()):
            raise OSError("依存DLLが見つからない")

    monkeypatch.setattr(libarchive_resolver, "_load_library", load)
    real_client = httpx.Client

    def client(**kwargs: typing.Any) -> httpx.Client:
        return real_client(transport=httpx.MockTransport(repository.handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    return install_dir, repository, path_writes


class _Records(logging.Handler):
    """導入処理のログ本文を集める。"""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _run(monkeypatch: pytest.MonkeyPatch) -> tuple[bool, str]:
    """導入処理を1回実行し、更新の有無とログを返す。プロセス環境の`LIBARCHIVE`は実行ごとに戻す。"""
    monkeypatch.delenv("LIBARCHIVE", raising=False)
    handler = _Records()
    install_libarchive.logger.addHandler(handler)
    try:
        outcome = install_libarchive.run()
    finally:
        install_libarchive.logger.removeHandler(handler)
    assert outcome.failure is None
    return outcome.changed, "\n".join(handler.messages)


def test_partial_placement_is_reinstalled(windows_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """旧版が残した本体DLLだけの配置は利用可能と扱わず、依存の取得へ進んで導入を完了する。"""
    install_dir, repository, path_writes = windows_env
    install_dir.mkdir()
    (install_dir / "libarchive-13.dll").write_bytes(b"old")

    changed, log = _run(monkeypatch)

    assert repository.requests, "取得へ進まなかった"
    assert changed
    assert "インストール完了" in log
    assert "既に利用可能" not in log
    assert (install_dir / "zlib1.dll").is_file()
    assert path_writes == [str(install_dir)]


@pytest.mark.parametrize("failure", ["unreachable", "broken", "missing"])
def test_rerun_after_interrupted_download_completes(windows_env, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    """後続パッケージの取得（HTTP）・展開・最新版の検出の失敗で本体だけ残っても、再実行で取得して完了する。"""
    install_dir, repository, path_writes = windows_env
    getattr(repository, failure).add("pkg-zlib")

    first_changed, first_log = _run(monkeypatch)

    assert not first_changed
    assert (install_dir / "libarchive-13.dll").is_file()
    assert "インストール完了" not in first_log
    assert path_writes == []
    getattr(repository, failure).clear()
    repository.requests.clear()

    changed, log = _run(monkeypatch)

    assert repository.requests
    assert changed
    assert "インストール完了" in log


def test_unloadable_after_download_is_not_reported_installed(windows_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """取得したパッケージが依存DLLを欠く場合は導入完了と表示せず、ユーザーPATHも登録しない。"""
    _install_dir, repository, path_writes = windows_env
    repository.omitted_dlls.add("zlib1.dll")

    changed, log = _run(monkeypatch)

    assert not changed
    assert "インストール完了" not in log
    assert "取得後もlibarchiveをロードできない" in log
    assert path_writes == []


def test_healthy_install_skips_download(windows_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """本体と依存が揃った既導入先はHTTP取得を行わず利用可能とする。"""
    install_dir, repository, _path_writes = windows_env
    install_dir.mkdir()
    for dll in _REQUIRED.values():
        (install_dir / dll).write_bytes(b"ok")

    changed, log = _run(monkeypatch)

    assert not changed
    assert repository.requests == []
    assert "既に利用可能" in log


def test_external_install_and_existing_libarchive_are_kept(
    windows_env, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自動配置先が無くても、既存の`LIBARCHIVE`が指す外部導入先をロードできれば取得しない。"""
    _install_dir, repository, _path_writes = windows_env
    external = tmp_path / "external"
    external.mkdir()
    for dll in _REQUIRED.values():
        (external / dll).write_bytes(b"ok")
    handler = _Records()
    install_libarchive.logger.addHandler(handler)
    monkeypatch.setenv("LIBARCHIVE", str(external / "libarchive-13.dll"))
    try:
        outcome = install_libarchive.run()
    finally:
        install_libarchive.logger.removeHandler(handler)

    assert outcome.failure is None
    assert repository.requests == []
    assert "既に利用可能" in "\n".join(handler.messages)
    assert libarchive_resolver.library_path() == str(external / "libarchive-13.dll")


def test_missing_dependency_after_install_is_detected_and_recovered(windows_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """導入後に依存DLLが欠けた配置は未利用可能として再取得し、正常な依存を戻せば利用可能になる。"""
    install_dir, repository, _path_writes = windows_env
    assert _run(monkeypatch)[0]
    (install_dir / "zlib1.dll").unlink()
    assert not libarchive_resolver.is_loadable(install_dir)
    repository.requests.clear()
    repository.broken.add("pkg-zlib")

    changed, log = _run(monkeypatch)

    assert repository.requests
    assert not changed
    assert "既に利用可能" not in log
    repository.broken.clear()
    changed, log = _run(monkeypatch)
    assert changed
    assert libarchive_resolver.is_loadable(install_dir)


def _repack(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, name: str) -> tuple[int, pathlib.Path, bytes]:
    """libarchive経路のアーカイブ（tar）を公開`repack_archive.main`で再パックし、終了コードと原本を返す。"""
    archive = tmp_path / f"{name}.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("page.txt")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"page"))
    original = archive.read_bytes()
    # 読み込みの成否は呼び出しごとに判定し直す（成功だけがキャッシュされる）。
    repack_archive._load_libarchive.cache_clear()  # pylint: disable=protected-access
    monkeypatch.setattr(sys, "argv", ["repack-archive", "--no-trash", str(archive)])
    with pytest.raises(SystemExit) as exc_info:
        repack_archive.main()
    code = exc_info.value.code
    assert isinstance(code, int)
    return code, archive, original


def test_consumer_detects_missing_dependency_and_recovers_after_reinstall(
    windows_env, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """依存DLLが欠けた導入先では公開`repack-archive`も読み込み失敗を検出し、再導入後は同じ入力を再パックできる。

    導入側と消費側の判定がずれると、導入は完了と表示されたままアーカイブ処理だけが失敗するか、その逆になる。
    """
    install_dir, repository, _path_writes = windows_env
    assert _run(monkeypatch)[0]
    (tmp_path / "work").mkdir()
    code, _archive, _original = _repack(tmp_path / "work", monkeypatch, "before")
    assert code == 0
    assert (tmp_path / "work" / "before.zip").is_file()

    (install_dir / "zlib1.dll").unlink()
    caplog.set_level(logging.ERROR, logger=repack_archive.logger.name)
    code, archive, original = _repack(tmp_path / "work", monkeypatch, "broken")
    assert code == 1
    assert archive.read_bytes() == original
    assert not (tmp_path / "work" / "broken.zip").exists()
    assert "libarchive の DLL を読み込めませんでした" in caplog.text

    repository.requests.clear()
    changed, log = _run(monkeypatch)
    assert repository.requests
    assert changed
    assert "インストール完了" in log
    code, _archive, _original = _repack(tmp_path / "work", monkeypatch, "after")
    assert code == 0
    assert (tmp_path / "work" / "after.zip").is_file()
