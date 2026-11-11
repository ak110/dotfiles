"""setup_miseのmise本体の探索・導入と更新。"""

import contextlib
import json
import ntpath
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

lazy import httpx

from pytools._internal import (
    common,
    log_format,
    post_apply_cache,
    setup_mise_common,
)


def ensure_mise_installed(client: httpx.Client | None = None) -> bool:
    """mise本体を導入し、後続のmise管理ツール導入を同じ実行で可能にする。

    mise不在を正常終了として扱うと管理ツールが全て未導入のまま残るため、不在時は
    Windowsではwinget、その他では公式インストーラーを使う。既存のself-update方針と
    矛盾させないため`MISE_VERSION`は渡さず、導入版を固定しない。
    """
    if setup_mise_common.is_windows():
        try:
            result = common.run_subprocess(
                [
                    "winget",
                    "install",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                    "--disable-interactivity",
                    "jdx.mise",
                ],
                timeout=setup_mise_common.MISE_INSTALL_TIMEOUT,
                tag="mise",
            )
        except Exception as error:  # noqa: BLE001  # 導入失敗で後続のセットアップ全体を止めない
            setup_mise_common.logger.warning(
                log_format.format_status(
                    "mise",
                    f"本体の導入に失敗: {error}。https://mise.jdx.dev/getting-started.html の手順でmiseを手動導入する",
                ),
            )
            return False
        if result is None or result.returncode != 0:
            setup_mise_common.logger.warning(
                log_format.format_status(
                    "mise",
                    f"本体の導入に失敗: {common.format_cli_error(result)}。"
                    "https://mise.jdx.dev/getting-started.html の手順でmiseを手動導入する",
                ),
            )
            return False
        return True

    temp_path: Path | None = None
    with contextlib.ExitStack() as resources:
        try:
            active_client = (
                client if client is not None else resources.enter_context(httpx.Client(timeout=30.0, follow_redirects=True))
            )
            response = active_client.get("https://mise.run")
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(mode="wb", suffix=".sh", delete=False) as temp:
                temp_path = Path(temp.name)
                temp.write(response.content)
            result = common.run_subprocess(
                ["sh", str(temp_path)],
                timeout=setup_mise_common.MISE_INSTALL_TIMEOUT,
                tag="mise",
                env_overrides={"MISE_INSTALL_PATH": str(Path.home() / ".local" / "bin" / "mise")},
            )
            if result is None or result.returncode != 0:
                setup_mise_common.logger.warning(
                    log_format.format_status(
                        "mise",
                        f"本体の導入に失敗: {common.format_cli_error(result)}。"
                        "https://mise.jdx.dev/getting-started.html の手順でmiseを手動導入する",
                    ),
                )
                return False
            return True
        except Exception as error:  # noqa: BLE001  # 取得・保存・実行の失敗を1件の警告へ集約する
            setup_mise_common.logger.warning(
                log_format.format_status(
                    "mise",
                    f"本体の導入に失敗: {error}。https://mise.jdx.dev/getting-started.html の手順でmiseを手動導入する",
                ),
            )
            return False
        finally:
            if temp_path is not None:
                with contextlib.suppress(OSError):
                    temp_path.unlink()


def find_mise_binary() -> Path | None:
    """Mise の実行ファイルを絶対パスで返す。

    現プロセスの PATH に無い場合 (Windows で User PATH 更新直後など) も看過しないよう、
    既知のインストールパスも併せて確認する。
    """
    from_path = shutil.which("mise")
    if from_path is not None:
        return Path(from_path).absolute()

    candidates: list[Path] = []
    if setup_mise_common.is_windows():
        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            candidates.append(Path(localappdata) / "Microsoft" / "WinGet" / "Links" / "mise.exe")
            candidates.append(Path(localappdata) / "mise" / "bin" / "mise.exe")
    else:
        candidates.append(Path.home() / ".local" / "bin" / "mise")

    for candidate in candidates:
        if candidate.is_file():
            return candidate.absolute()
    return None


# `mise self-update`は公式インストーラーで導入したmiseだけが使える。手動でも失敗する場合の代替手段。
_SELF_UPDATE_FALLBACK = "手動でも失敗する場合は、miseを導入した手段（パッケージマネージャーや公式インストーラー）で更新する"

# WinGetの`upgrade`が「適用できる更新がない」ときに返す終了コード（APPINSTALLER_CLI_ERROR_UPDATE_NOT_APPLICABLE）。
# Windowsの`returncode`は符号なしの値で返るが、符号付きで返る実行系にも対応する。
_WINGET_UPDATE_NOT_APPLICABLE = frozenset({0x8A15002B, 0x8A15002B - (1 << 32)})


def _is_winget_managed(mise_bin: Path) -> bool:
    r"""Mise実行ファイルがWinGetの配置先（`WinGet\\Links`や`WinGet\\Packages`）にあるかを返す。

    WinGetで導入したmiseは`mise self-update`を拒否するため、更新操作の選択に使う。
    PATHで別の配置のmiseが先行する場合は、実際に使う実行ファイルの配置で判定する。
    """
    if not setup_mise_common.is_windows():
        return False
    parts = [part.lower() for part in ntpath.normpath(str(mise_bin)).split("\\")]
    # WinGetは`%LOCALAPPDATA%\Microsoft\WinGet\{Links,Packages}`へ配置する。ユーザー名など
    # 別の位置にある`winget`という名前のディレクトリを取り違えないよう、連続する3要素で判定する。
    winget_dirs = (["microsoft", "winget", "links"], ["microsoft", "winget", "packages"])
    return any(parts[i : i + 3] in winget_dirs for i in range(len(parts)))


def ensure_mise_up_to_date(mise_bin: Path) -> bool:
    """Mise本体を、導入した手段に合う操作で最新版へ更新する。

    WinGetで導入したmiseは`mise self-update`を拒否するため`winget upgrade`で更新し、
    それ以外は`mise self-update -y`で更新する。
    更新の失敗は警告して吸収し、後続ステップ（trust・global node・tools install）は継続する。
    """
    if _is_winget_managed(mise_bin):
        current = setup_mise_common.run_mise(mise_bin, ["--version"])
        match = re.search(r"\b(\d{4}\.\d+\.\d+)\b", current.stdout) if current is not None and current.returncode == 0 else None
        if match is not None:
            record = common.load_json_dict(post_apply_cache.record_path("mise-winget"))
            if record is not None and record.get("version") == match[1]:
                checked = record.get("checked")
                if isinstance(checked, (int, float)) and 0 <= time.time() - checked < 86400:
                    return False
            candidate = common.run_subprocess(
                [
                    "winget",
                    "show",
                    "--id",
                    "jdx.mise",
                    "--exact",
                    "--versions",
                    "--accept-source-agreements",
                    "--disable-interactivity",
                ],
                timeout=setup_mise_common.MISE_TIMEOUT,
                tag="mise",
            )
            versions = (
                re.findall(r"(?m)^\s*(\d{4}\.\d+\.\d+)\s*$", common.strip_ansi(candidate.stdout))
                if candidate is not None and candidate.returncode == 0
                else []
            )
            if versions and tuple(map(int, match[1].split("."))) >= max(
                tuple(map(int, value.split("."))) for value in versions
            ):
                common.atomic_write_text(
                    post_apply_cache.record_path("mise-winget"),
                    json.dumps({"version": match[1], "checked": time.time()}),
                    tag="mise",
                )
                setup_mise_common.logger.info(log_format.format_status("mise", "本体がWinGet候補版以上のため更新を省略"))
                return False
        return _upgrade_mise_with_winget()
    result = setup_mise_common.run_mise(mise_bin, ["self-update", "-y"], timeout=setup_mise_common.MISE_INSTALL_TIMEOUT)
    if result is None:
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                "`self-update` がタイムアウトまたは例外で中断（処理は継続した）。`mise self-update -y`を手動実行する。"
                f"{_SELF_UPDATE_FALLBACK}",
            ),
        )
        return False
    if result.returncode != 0:
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                setup_mise_common.with_mise_output(
                    f"`self-update` に失敗（処理は継続した）。`mise self-update -y`を手動実行する。{_SELF_UPDATE_FALLBACK}",
                    result,
                ),
            ),
        )
        return False
    setup_mise_common.logger.info(log_format.format_status("mise", "`self-update` を実行しました"))
    return True


def _upgrade_mise_with_winget() -> bool:
    """WinGetで導入したmiseを`winget upgrade`で更新する。

    適用できる更新がない場合は更新なしとして扱い、その他の失敗は警告して後続ステップを継続する。
    """
    manual = (
        "`winget upgrade --id jdx.mise --exact`を手動実行する。"
        "手動でも失敗する場合は、表示されたwingetの出力に従ってWinGetの状態を確かめる"
    )
    try:
        result = common.run_subprocess(
            [
                "winget",
                "upgrade",
                "--id",
                "jdx.mise",
                "--exact",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "--disable-interactivity",
            ],
            timeout=setup_mise_common.MISE_INSTALL_TIMEOUT,
            tag="mise",
        )
    except Exception as error:  # noqa: BLE001  # 更新失敗で後続のセットアップ全体を止めない
        setup_mise_common.logger.warning(
            log_format.format_status("mise", f"`winget upgrade` に失敗（処理は継続した）: {error}。{manual}")
        )
        return False
    if result is None:
        setup_mise_common.logger.warning(
            log_format.format_status("mise", f"`winget upgrade` がタイムアウトまたは例外で中断（処理は継続した）。{manual}"),
        )
        return False
    if result.returncode in _WINGET_UPDATE_NOT_APPLICABLE:
        setup_mise_common.logger.info(log_format.format_status("mise", "WinGetに適用できる更新が無いため本体は現在の版のまま"))
        return False
    if result.returncode != 0:
        output = common.format_cli_error(result)
        if "remove_all" in output and "access is denied" in output.lower():
            manual = (
                "miseの配置先を使用中の可能性があるため、miseを使うターミナルやエディターを終了し、"
                "新しいターミナルで`update-dotfiles`を再実行する。プロセスの強制終了や自動昇格は行わない"
            )
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                f"`winget upgrade` に失敗（処理は継続した）。{manual}\n{common.format_cli_error(result)}",
            ),
        )
        return False
    setup_mise_common.logger.info(log_format.format_status("mise", "`winget upgrade` で本体を更新しました"))
    return True


# 同梱のgcloudプラグインが`require("python")`を解決できず、gcloudの導入と実行が失敗するmiseの版（jdx/mise#15034）。
_GCLOUD_PLUGIN_BROKEN_VERSIONS = frozenset({"2026.10.20", "2026.10.21"})

# 同梱版の代わりに使うGit配布のgcloudプラグイン。ファイルシステムから読み込むプラグインは`hooks/`配下の
# モジュールを解決できるため、上流のissueが回避策として示している。
_GCLOUD_PLUGIN_URL = "https://github.com/jdx/vfox-gcloud"


def ensure_gcloud_plugin_usable(mise_bin: Path) -> bool:
    """同梱gcloudプラグインが壊れた版のmiseでは、Git配布のプラグインを導入して置き換える。

    WinGetなど管理元の配布が修正版より遅れると本体を更新しても壊れた版が残るため、版で判定する。
    導入済みなら再導入しない。導入の失敗は警告し、後続の`mise install`へ進む。

    Returns:
        プラグインを新たに導入した場合True。
    """
    version_result = setup_mise_common.run_mise(mise_bin, ["--version"])
    if version_result is None or version_result.returncode != 0:
        return False
    match = re.search(r"\b(\d{4}\.\d+\.\d+)\b", version_result.stdout)
    if match is None:
        return False
    # 更新後に実際に使う本体の版と実体を記録し、更新経路と回避処置の要否をログで確かめられるようにする。
    setup_mise_common.logger.info(log_format.format_status("mise", f"本体 {match[1]} を使う: {mise_bin}"))
    if match[1] not in _GCLOUD_PLUGIN_BROKEN_VERSIONS:
        return False

    listed = setup_mise_common.run_mise(mise_bin, ["plugins", "ls", "--urls"])
    if listed is not None and listed.returncode == 0:
        for line in listed.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] == "gcloud" and fields[1].removesuffix(".git") == _GCLOUD_PLUGIN_URL:
                return False

    result = setup_mise_common.run_mise(
        mise_bin, ["plugins", "install", "gcloud", _GCLOUD_PLUGIN_URL], timeout=setup_mise_common.MISE_INSTALL_TIMEOUT
    )
    if result is None or result.returncode != 0:
        setup_mise_common.logger.warning(
            log_format.format_status(
                "mise",
                setup_mise_common.with_mise_output(
                    f"mise {match[1]}の同梱gcloudプラグインの不具合を回避するGit配布プラグインの導入に失敗"
                    f"（処理は継続した）。`mise plugins install gcloud {_GCLOUD_PLUGIN_URL}`を手動実行するか、"
                    "miseを2026.10.22以降へ更新する",
                    result,
                ),
            ),
        )
        return False
    setup_mise_common.logger.info(
        log_format.format_status(
            "mise",
            f"mise {match[1]}の同梱gcloudプラグインの不具合を回避するため、Git配布のプラグインを導入しました",
        ),
    )
    return True
