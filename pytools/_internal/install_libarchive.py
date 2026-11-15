"""Windows向けlibarchive.dllインストーラー。

`chezmoi apply`後処理（`pytools.post_apply`）から呼ばれ、
MSYS2リポジトリからlibarchiveと動的依存パッケージのDLLを取得・配置する。
"""

import compression.zstd
import dataclasses
import hashlib
import io
import logging
import ntpath
import pathlib
import re
import tarfile

from pytools._internal import libarchive_resolver, log_format, post_apply_outcome, winutils

logger = logging.getLogger(__name__)

# MSYS2 の mingw64 リポジトリ。カーリング追跡が容易なように定数化する。
_MSYS2_REPO = "https://repo.msys2.org/mingw/mingw64/"

_ROOT_PACKAGE = "mingw-w64-x86_64-libarchive"

_INSTALL_DIR = libarchive_resolver.INSTALL_DIR
_HTTP_TIMEOUT = 60.0
# 展開後のtarを全量メモリへ置くため、壊れた入力や想定外の巨大パッケージで際限なく確保しないよう上限を設ける。
_MAX_DECOMPRESSED_SIZE = 512 * 1024 * 1024


@dataclasses.dataclass(frozen=True)
class _Package:
    """公式DBが指定した取得物と実行時依存。"""

    filename: str
    sha256: str
    dependencies: tuple[str, ...]


def run() -> post_apply_outcome.PostApplyOutcome:
    """libarchive.dllを配置し、ユーザーPATHと``LIBARCHIVE``環境変数を登録する。

    導入済みの判定は、消費側（`repack-archive`）と同じDLL探索条件で実際にロードできるかで行う。
    本体DLLだけが残った部分配置や依存DLLの欠落はロードに失敗するため、取得へ進む。
    取得後も同じ判定でロードできた場合だけ導入完了とする。
    ``LIBARCHIVE`` 環境変数の永続化は、DLLの有無にかかわらず毎回実施する。
    libarchive-cはWindowsでDLLを解決する際に ``LIBARCHIVE`` 環境変数を最優先で
    参照するため、DLLだけ配置されて環境変数が未設定の環境でも正しく動作させるためのワークアラウンドである。
    DLLの取得と配置の失敗はスキップ、ユーザーPATHと環境変数の書き込みの失敗は失敗と数える。
    """
    changed = False
    if _is_loadable():
        logger.info(log_format.format_status("libarchive", f"既に利用可能 ({_INSTALL_DIR})"))
    else:
        try:
            installed = _download_dlls()
        except Exception as e:  # noqa: BLE001 -- 取得と展開のどの段の失敗も導入の失敗として扱う
            logger.warning(
                log_format.format_status(
                    "libarchive",
                    f"自動インストールに失敗: {e}。{_MSYS2_REPO}のmingw64.dbと"
                    f"{_ROOT_PACKAGE}の実行時依存パッケージの公開状況・接続を確認し、"
                    "update-dotfilesを再実行してください。",
                )
            )
            return post_apply_outcome.PostApplyOutcome()
        if not installed:
            return post_apply_outcome.PostApplyOutcome()
        if not _is_loadable():
            logger.warning(
                log_format.format_status(
                    "libarchive",
                    f"取得後もlibarchiveをロードできないため導入を完了していません（{_INSTALL_DIR}）。"
                    "依存DLLの欠落か取得したパッケージの不整合が考えられます。"
                    f"{_INSTALL_DIR}の内容を確認し、update-dotfilesを再実行してください。",
                )
            )
            return post_apply_outcome.PostApplyOutcome()
        try:
            winutils.append_user_path(str(_INSTALL_DIR))
        except OSError as e:
            return post_apply_outcome.PostApplyOutcome(
                failure=f"ユーザー PATH への登録に失敗: {e}",
                next_action=f"Windowsのユーザー環境変数のPathへ{_INSTALL_DIR}を追加して、update-dotfilesを再実行してください。",
            )
        logger.info(log_format.format_status("libarchive", f"インストール完了: {_INSTALL_DIR}"))
        changed = True
    try:
        if _persist_libarchive_env_var():
            changed = True
    except OSError as e:
        return post_apply_outcome.PostApplyOutcome(
            changed=changed,
            failure=f"LIBARCHIVE 環境変数の設定に失敗: {e}",
            next_action=(
                f"Windowsのユーザー環境変数LIBARCHIVEを{_INSTALL_DIR / 'libarchive-13.dll'}へ設定して、"
                "update-dotfilesを再実行してください。"
            ),
        )
    return post_apply_outcome.PostApplyOutcome(changed=changed)


def _download_dlls() -> bool:
    """公式DBのパッケージと推移依存を取得し、ハッシュを照合してDLLを配置する。"""
    import httpx  # pylint: disable=import-outside-toplevel

    _INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        response = client.get(f"{_MSYS2_REPO}mingw64.db")
        response.raise_for_status()
        packages = _resolve_packages(_read_database(response.content), _ROOT_PACKAGE)
        for package in packages:
            logger.info(log_format.format_status("libarchive", f"downloading {package.filename}"))
            response = client.get(f"{_MSYS2_REPO}{package.filename}")
            response.raise_for_status()
            data = response.content
            if hashlib.sha256(data).hexdigest() != package.sha256:
                raise ValueError(f"SHA256が公式DBと一致しない: {package.filename}")
            _extract_dlls(data)
    return True


def _is_loadable() -> bool:
    """消費側と同じ探索条件でlibarchiveをロードできるかを判定する。"""
    return libarchive_resolver.is_loadable(_INSTALL_DIR)


def _read_database(data: bytes) -> dict[str, _Package]:
    """MSYS2のdescから、取得と実行時依存の解決に必要な値を読み取る。"""
    packages = {}
    with tarfile.open(fileobj=io.BytesIO(_decompress_zst(data)), mode="r:") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith("/desc"):
                continue
            stream = tar.extractfile(member)
            assert stream is not None
            with stream:
                blocks = stream.read().decode("utf-8").strip().split("\n\n")
            fields = {lines[0]: lines[1:] for block in blocks if (lines := block.splitlines())}
            (name,) = fields["%NAME%"]
            (filename,) = fields["%FILENAME%"]
            (sha256,) = fields["%SHA256SUM%"]
            if not re.fullmatch(r"[A-Za-z0-9_+~.@-]+\.pkg\.tar\.zst", filename):
                raise ValueError(f"不正なパッケージファイル名: {filename}")
            if not re.fullmatch(r"[0-9a-f]{64}", sha256) or name in packages:
                raise ValueError(f"不正または重複したパッケージ情報: {name}")
            dependencies = tuple(re.split(r"[<>=]", item, maxsplit=1)[0] for item in fields.get("%DEPENDS%", []))
            packages[name] = _Package(filename, sha256, dependencies)
    return packages


def _resolve_packages(database: dict[str, _Package], root: str) -> list[_Package]:
    """DBの現在版だけを使い、版条件を除いた正確な名前で推移依存を各1回選ぶ。"""
    pending = [root]
    seen = set()
    packages = []
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        if name not in database:
            raise ValueError(f"公式DBに実行時依存パッケージがない: {name}")
        seen.add(name)
        package = database[name]
        packages.append(package)
        pending.extend(reversed(package.dependencies))
    return packages


def _decompress_zst(data: bytes) -> bytes:
    """zstd圧縮された単一フレームを標準ライブラリで上限付きで展開する。"""
    decompressor = compression.zstd.ZstdDecompressor()
    raw = decompressor.decompress(data, max_length=_MAX_DECOMPRESSED_SIZE)
    if not decompressor.eof:
        raise ValueError(f"展開後のサイズが上限 {_MAX_DECOMPRESSED_SIZE} バイトを超えるか、zstdフレームが不完全")
    return raw


def _extract_dlls(pkg_data: bytes) -> None:
    """pkg.tar.zst のバイト列を展開し、DLL だけを `_INSTALL_DIR` へ配置する。"""
    raw = _decompress_zst(pkg_data)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
        for member in tar.getmembers():
            if not (member.isfile() or member.islnk()):
                continue
            if not member.name.lower().endswith(".dll"):
                continue
            if "/bin/" not in member.name.replace("\\", "/"):
                continue
            dll_name = pathlib.PurePosixPath(member.name.replace("\\", "/")).name
            if pathlib.PureWindowsPath(dll_name).name != dll_name or ntpath.isreserved(dll_name):
                raise ValueError(f"不正なDLLファイル名: {dll_name}")
            source = _resolve_dll_member(tar, member)
            stream = tar.extractfile(source)
            assert stream is not None
            with stream:
                (_INSTALL_DIR / dll_name).write_bytes(stream.read())


def _resolve_dll_member(tar: tarfile.TarFile, member: tarfile.TarInfo) -> tarfile.TarInfo:
    """ハードリンクをアーカイブ内だけで解決し、欠落・循環・通常ファイル以外を拒否する。"""
    seen = set()
    while member.islnk():
        if member.name in seen:
            raise ValueError(f"DLLのハードリンクが循環している: {member.name}")
        seen.add(member.name)
        try:
            member = tar.getmember(member.linkname)
        except KeyError as error:
            raise ValueError(f"DLLのハードリンク先がない: {member.linkname}") from error
    if not member.isfile():
        raise ValueError(f"DLLのリンク先が通常ファイルでない: {member.name}")
    return member


def _persist_libarchive_env_var() -> bool:
    """ユーザースコープの ``LIBARCHIVE`` 環境変数を永続化する。

    libarchive-cの ``ffi.py`` はDLL探索時にまず ``LIBARCHIVE`` 環境変数を参照する。
    `_INSTALL_DIR` 配下の ``libarchive-13.dll`` を直接指すことで、Windowsの
    ``find_library("archive")`` で解決できないファイル名でも解決できる。

    挙動:

    - DLLが未配置なら何もしない（``False`` を返す）。
    - 既に同じ値が設定されていればスキップする。
    - 既存値が別パスを指している場合は上書きせず警告ログのみ出力する
      （ユーザーが別途libarchiveを導入している可能性に配慮）。
    - 書き換えに成功したら ``True`` を返す。

    Returns:
        レジストリを実際に書き換えた場合に ``True``。
    """
    target_dll = _INSTALL_DIR / libarchive_resolver.DLL_NAME
    if not target_dll.exists():
        return False
    target = str(target_dll)
    current, reg_type = winutils.read_user_env_var("LIBARCHIVE")
    if current is None:
        current = ""
        reg_type = winutils.import_winreg().REG_SZ
    if current == target:
        return False
    if current:
        logger.info(
            log_format.format_status(
                "libarchive",
                f"LIBARCHIVE は既に {current} を指しているため上書きしない",
            )
        )
        return False
    winutils.write_user_env_var("LIBARCHIVE", target, reg_type)
    logger.info(log_format.format_status("libarchive", f"LIBARCHIVE 環境変数を設定: {target}"))
    return True
