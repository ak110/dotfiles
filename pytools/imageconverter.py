# PYTHON_ARGCOMPLETE_OK
"""画像変換。

ディレクトリ・ファイル個別・ワイルドカードを指定して画像を一括変換し、最大辺サイズを超える場合は縮小する。
CLIからは`py-imageconverter`コマンドとして、他モジュールからは`convert_directory`関数として呼び出せる。
"""

import argparse
import contextlib
import dataclasses
import glob
import logging
import pathlib
import secrets
import sys
import typing
import warnings

import natsort
import numpy as np
import PIL.Image
import PIL.ImageFile
import PIL.ImageOps
import tqdm

from pytools._internal.cli import enable_completion

logger = logging.getLogger(__name__)

_IGNORE_SUFFIXES = {".db", ".txt", ".htm", ".html", ".pdf", ".bat"}
_TYPE_SUFFIXES = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}
_WILDCARD_CHARS = ("*", "?", "[")

OutputType = typing.Literal["jpeg", "png", "webp"]
EventSeverity = typing.Literal["warning", "error"]
ConvertEvent = tuple[pathlib.Path, EventSeverity, str]


@dataclasses.dataclass
class ConvertSummary:
    """画像変換の集計結果。"""

    events: list[ConvertEvent]
    success_count: int


def open_image_with_exif(path: pathlib.Path) -> tuple[PIL.Image.Image, bool]:
    """EXIF情報に従い回転補正した画像と寛容モードフラグを返す。

    Pillow 12.xはPNG補助チャンク（tEXt等）のCRCが不整合だと既定で`UnidentifiedImageError`を
    送出して`Image.open`が失敗するが、ImageMagick等の他ツールでは警告レベルで読み込めるケースがある。
    まず既定モードで`PIL.Image.open`を試み、シグネチャ判定または本体読み込みで失敗した場合のみ
    `PIL.ImageFile.LOAD_TRUNCATED_IMAGES = True`へ切り替えて再試行する。再試行で成功した場合は
    第2戻り値を`True`として返す。

    `PIL.ImageOps.exif_transpose`が例外を送出した場合は元画像のコピーへフォールバックする。

    Raises:
        PIL.UnidentifiedImageError: `Image.open`段階で発生し、寛容モードでも回復しなかった場合に伝播する。
            「非画像」を示す。
        OSError: `Image.open`または`img.load`段階で発生し、寛容モードでも回復しなかった場合に元の例外型のまま伝播する。
            シグネチャ判定は通過したが本体読み込みに失敗したことを示す。
        SyntaxError: `img.load`段階で発生し、寛容モードでも回復しなかった場合に同様に伝播する。

    Returns:
        `(image, used_truncated_fallback)`のタプル。第2要素が`True`のとき、
        Pillowが既定モードで読み込みに失敗し寛容モードへフォールバックしたことを示す。
    """
    used_fallback = False
    try:
        img = PIL.Image.open(path)
    except PIL.UnidentifiedImageError:
        # シグネチャ判定で非画像と判定された場合でも寛容モードでは読み込める入力があるため再試行する。
        # 寛容モードでも回復しない場合は UnidentifiedImageError をそのまま伝播させる。
        with _truncated_images_enabled():
            img = PIL.Image.open(path)
            img.load()
        used_fallback = True
    else:
        try:
            img.load()
        except (OSError, SyntaxError):
            # シグネチャ判定は通過したが本体読み込みで失敗した場合は寛容モードで再試行する。
            # 寛容モードでも失敗した場合は元の例外型のまま伝播させる。
            with _truncated_images_enabled():
                img = PIL.Image.open(path)
                img.load()
            used_fallback = True

    try:
        result = PIL.ImageOps.exif_transpose(img)
    except Exception as e:  # noqa: BLE001
        warnings.warn(f"{type(e).__name__}: {e}", stacklevel=2)
        result = img.copy()
    return result, used_fallback


@contextlib.contextmanager
def _truncated_images_enabled() -> typing.Iterator[None]:
    """`PIL.ImageFile.LOAD_TRUNCATED_IMAGES` を True にして実行するコンテキスト。"""
    prev = PIL.ImageFile.LOAD_TRUNCATED_IMAGES
    # ty は初期値 ``False`` をリテラル型として推論し、True 代入を拒否するが、
    # Pillow 側はこのフラグを書き換え可能なグローバル変数として公開している。
    PIL.ImageFile.LOAD_TRUNCATED_IMAGES = True  # ty: ignore[invalid-assignment]
    try:
        yield
    finally:
        PIL.ImageFile.LOAD_TRUNCATED_IMAGES = prev


def main() -> None:
    """画像を一括変換するエントリポイント。"""
    parser = argparse.ArgumentParser(description="画像変換")
    parser.add_argument("--output-type", default="jpeg", choices=("jpeg", "png", "webp"), nargs="?")
    parser.add_argument("--max-width", default=2048, type=int)
    parser.add_argument("--max-height", default=1536, type=int)
    parser.add_argument("--jpeg-quality", default=90, type=int)
    parser.add_argument("--repack-png", action="store_true")
    parser.add_argument("--no-remove-failed", action="store_true")
    parser.add_argument("targets", nargs="+", type=str)
    enable_completion(parser)
    args = parser.parse_args()

    # 明示ディレクトリ指定の再帰探索結果と他経路（ワイルドカード展開・別target指定）の
    # 同一ファイルが結果集合に混在して二重処理されるのを防ぐため、Path.resolve()をキーに重複排除する。
    seen_keys: set[pathlib.Path] = set()
    files: list[pathlib.Path] = []
    for target in args.targets:
        expanded = _expand_target(target)
        if not expanded:
            logger.warning("対象が見つからない: %s", target)
            continue
        for path in expanded:
            if path.suffix.lower() in _IGNORE_SUFFIXES:
                continue
            key = path.resolve()
            if key in seen_keys:
                continue
            seen_keys.add(key)
            files.append(path)

    summary = convert_paths(
        files,
        output_type=args.output_type,
        max_width=args.max_width,
        max_height=args.max_height,
        jpeg_quality=args.jpeg_quality,
        repack_png=args.repack_png,
        remove_failed=not args.no_remove_failed,
    )
    has_error = any(severity == "error" for _, severity, _ in summary.events)
    if summary.success_count == 0:
        logger.error("変換成功 0 件")
    exit_code = 1 if (has_error or summary.success_count == 0) else 0
    sys.exit(exit_code)


def _expand_target(target: str) -> list[pathlib.Path]:
    """target文字列をパス列へ展開する。

    ワイルドカード（`*`・`?`・`[`）含有時は`glob.glob`で展開しファイルのみ採用する。
    未含有時はそのままパス化し、ファイルなら単一、ディレクトリなら配下を再帰展開する。
    """
    if any(c in target for c in _WILDCARD_CHARS):
        # Windowsの`cmd`は`*`を展開しないため、Linuxシェル相当の展開をPython側で行う。
        # `**`を書けば再帰、`*`なら非再帰の挙動になる。
        results: list[pathlib.Path] = []
        for raw in glob.glob(target, recursive=True):
            path = pathlib.Path(raw)
            # `*`の非再帰仕様を保つため、ワイルドカード由来のディレクトリは再帰展開しない。
            if path.is_file():
                results.append(path)
        return results

    path = pathlib.Path(target)
    if path.is_file():
        return [path]
    if path.is_dir():
        return [child for child in path.glob("**/*") if child.is_file()]
    return []


def convert_directory(
    target_path: pathlib.Path,
    *,
    output_type: OutputType = "jpeg",
    max_width: int = 2048,
    max_height: int = 1536,
    jpeg_quality: int = 90,
    repack_png: bool = False,
    remove_failed: bool = True,
    progress: tqdm.tqdm | None = None,
    log_root: pathlib.Path | None = None,
) -> ConvertSummary:
    """ディレクトリまたは単一ファイルの画像を変換する。

    ディレクトリを渡した場合は配下を再帰探索する。ファイルを渡した場合は当該1件を処理する。
    `_IGNORE_SUFFIXES`に該当する拡張子はスキップする。

    Args:
        target_path: 処理対象のディレクトリまたはファイル。
        output_type: 出力形式。
        max_width: 出力最大幅（これを超える場合は縮小する）。
        max_height: 出力最大高さ（これを超える場合は縮小する）。
        jpeg_quality: JPEG出力時の品質。
        repack_png: PNG入力時にwandで再パックしてメタデータを除去する。
        remove_failed: 変換に失敗したファイルを削除する。
        progress: 呼び元で作成済みのtqdm。未指定なら内部で作成する。
        log_root: 進捗ログのパス表示で起点にするディレクトリ。指定時はPOSIX区切りの
            相対パスで表示する。未指定時は絶対パスのまま表示する。

    Returns:
        画像変換の集計結果（`ConvertSummary`）。`events`は警告・失敗イベント一覧、
        `success_count`は変換成功（warning含む）数。
    """
    if target_path.is_file():
        candidates: list[pathlib.Path] = [target_path]
    else:
        candidates = [p for p in target_path.glob("**/*") if p.is_file()]
    paths = [p for p in candidates if p.suffix.lower() not in _IGNORE_SUFFIXES]
    return convert_paths(
        paths,
        output_type=output_type,
        max_width=max_width,
        max_height=max_height,
        jpeg_quality=jpeg_quality,
        repack_png=repack_png,
        remove_failed=remove_failed,
        progress=progress,
        log_root=log_root,
    )


def convert_paths(
    paths: typing.Iterable[pathlib.Path],
    *,
    output_type: OutputType = "jpeg",
    max_width: int = 2048,
    max_height: int = 1536,
    jpeg_quality: int = 90,
    repack_png: bool = False,
    remove_failed: bool = True,
    progress: tqdm.tqdm | None = None,
    log_root: pathlib.Path | None = None,
) -> ConvertSummary:
    """ファイル列を順次変換する共通ループ。

    無視対象拡張子のフィルタと重複排除は呼び出し側で済ませた前提で受け取り、
    自然順ソート後にtqdmループで各ファイルを処理する。

    Returns:
        画像変換の集計結果（`ConvertSummary`）。
    """
    suffix = _TYPE_SUFFIXES[output_type]
    sorted_paths = list(natsort.natsorted(paths))
    pbar = (
        progress
        if progress is not None
        else tqdm.tqdm(total=len(sorted_paths), desc="convert", unit="file", ascii=True, ncols=100)
    )
    events: list[ConvertEvent] = []
    success_count = 0
    try:
        for path in sorted_paths:
            event, ok = _convert_one(
                path,
                suffix=suffix,
                output_type=output_type,
                max_width=max_width,
                max_height=max_height,
                jpeg_quality=jpeg_quality,
                repack_png=repack_png,
                remove_failed=remove_failed,
                log_root=log_root,
            )
            if event is not None:
                events.append(event)
            if ok:
                success_count += 1
            pbar.update(1)
    finally:
        if progress is None:
            pbar.close()
    return ConvertSummary(events=events, success_count=success_count)


def _convert_one(
    path: pathlib.Path,
    *,
    suffix: str,
    output_type: OutputType,
    max_width: int,
    max_height: int,
    jpeg_quality: int,
    repack_png: bool,
    remove_failed: bool,
    log_root: pathlib.Path | None = None,
) -> tuple[ConvertEvent | None, bool]:
    """1ファイル分の変換処理。

    Returns:
        `(event, ok)`のタプル。`ok`は処理成功（warning含む）を示す。
        - 変換成功時: `(None, True)`、寛容モードフォールバック時は`(warning_event, True)`
        - 非画像と判定されてスキップした場合: `(None, False)`
        - 画像認識後の変換失敗時: `(error_event, False)`
    """
    try:
        if repack_png and path.suffix.lower() == ".png":
            temp_png_path = path.parent / f"{secrets.token_urlsafe(8)}.png"
            _repack_png(path, temp_png_path)
            path.unlink()
            temp_png_path.rename(path)
        # Pillow自身がシグネチャ判定するため拡張子フィルタは設けない。
        # 非画像と判定されたファイルは削除せずスキップする。
        try:
            img, used_fallback = open_image_with_exif(path)
        except PIL.UnidentifiedImageError:
            return None, False
        with img:
            if output_type == "jpeg":
                if img.mode == "RGBA":
                    img = img.convert("RGB")
                elif img.mode == "LA":
                    img = img.convert("L")
            if img.width >= max_width or img.height >= max_height:
                r = min(max_width / img.width, max_height / img.height)
                size = int(img.width * r), int(img.height * r)
                img = img.resize(size, resample=PIL.Image.Resampling.LANCZOS)
            # メタデータを削除して保存する
            with PIL.Image.fromarray(np.asarray(img)) as img2:
                temp_path = path.parent / f"{secrets.token_urlsafe(8)}{suffix}"
                if output_type == "jpeg":
                    img2.save(temp_path, format="JPEG", quality=jpeg_quality)
                else:
                    img2.save(temp_path)
    except Exception as e:  # noqa: BLE001
        # Pillowが画像と認識したファイルのみ変換失敗扱いで`remove_failed`に従って削除する。
        message = f"convert failed ({e})"
        tqdm.tqdm.write(f"{_format_log_path(path, log_root)}: {message}")
        if remove_failed:
            path.unlink()
        return (path, "error", message), False
    # 元ファイルを削除し、リネームする（バックアップされている前提）
    path.unlink()
    save_path = path.with_suffix(suffix)
    temp_path.rename(save_path)
    tqdm.tqdm.write(_format_log_path(save_path, log_root))
    if used_fallback:
        message = "LOAD_TRUNCATED_IMAGES フォールバックで読み込んだ"
        tqdm.tqdm.write(f"{_format_log_path(save_path, log_root)}: warning ({message})")
        return (save_path, "warning", message), True
    return None, True


def _format_log_path(path: pathlib.Path, log_root: pathlib.Path | None) -> str:
    """進捗ログのパス表示を整形する。

    ``log_root`` が指定され、かつ ``path`` がその配下にある場合のみPOSIX区切りの
    相対パスへ変換する。それ以外は絶対パス文字列をそのまま返す。
    """
    if log_root is None:
        return str(path)
    try:
        return path.relative_to(log_root).as_posix()
    except ValueError:
        return str(path)


def _repack_png(input_path: pathlib.Path, output_path: pathlib.Path) -> None:
    """PNGを再パックして不要なチャンクを削除する。wand（ImageMagick）が必要。"""
    try:
        # 重量級optional依存のため遅延import。
        import wand.color  # pylint: disable=import-outside-toplevel
        import wand.image  # pylint: disable=import-outside-toplevel
    except ImportError:
        raise RuntimeError(
            "--repack-png を使用するには wand パッケージと ImageMagick のインストールが必要です。\n"
            "  pip install wand  # または uv pip install wand\n"
            "  # ImageMagick: https://imagemagick.org/script/download.php"
        ) from None
    with wand.image.Image(filename=str(input_path)) as img:
        assert img.options is not None
        img.options["png:exclude-chunk"] = "all"
        img.strip()
        with wand.color.Color("transparent"):
            img.save(filename=str(output_path))


if __name__ == "__main__":
    main()
