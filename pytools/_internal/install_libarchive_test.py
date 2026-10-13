"""install_libarchiveのパッケージ展開のテスト。"""

import compression.zstd
import io
import pathlib
import tarfile

import pytest

from pytools._internal import install_libarchive


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
