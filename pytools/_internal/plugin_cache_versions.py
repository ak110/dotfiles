"""plugin cacheの版ディレクトリを導入順に並べ、後継版の導入から猶予期間を過ぎた版を選ぶ。

Claude CodeとCodexはどちらも`<cache>/<marketplace>/<name>/<version>/`へ版ごとに導入し、
セッションは起動時に解決した版の絶対パスを再開後も使い続ける。
後継版の導入時刻はその版が現行でなくなった時刻であり、そこから猶予期間を過ぎた版を回収の候補にする。
稼働中の参照の判定は、ホストごとの回収処理が行う。
"""

from collections.abc import Iterable
from pathlib import Path

# 後継版の導入から旧版を回収の候補にするまでの日数。
# 2026年11月19日の観測では、Codexの会話記録（rollout）1万1千件の開始から最終書込までが最長3.2日だった。
GRACE_DAYS = 7


def expired(version_dirs: Iterable[Path], keep: set[Path], deadline: float, manifest: str) -> list[Path]:
    """後継版の導入時刻が`deadline`以前である版ディレクトリを、`keep`（実体パス）を除いて返す。

    導入時刻は版ディレクトリ配下の`manifest`のmtimeとする。後継の無い最新の版は返さない。
    """
    timed = sorted(((_installed_at(path, manifest), path) for path in version_dirs), key=lambda item: (item[0], item[1].name))
    result: list[Path] = []
    for (_, version_dir), (successor_installed_at, _) in zip(timed, timed[1:], strict=False):
        if _resolve(version_dir) not in keep and successor_installed_at <= deadline:
            result.append(version_dir)
    return result


def _installed_at(version_dir: Path, manifest: str) -> float:
    """版ディレクトリの導入時刻を返す。

    版ディレクトリ自体のmtimeは導入後に`.venv`などを作るたびに更新されるため、導入時に書かれる`manifest`のmtimeを使う。
    `manifest`を欠く版だけは版ディレクトリのmtimeで代替する。
    """
    try:
        return (version_dir / manifest).stat().st_mtime
    except OSError:
        pass
    try:
        return version_dir.stat().st_mtime
    except OSError:
        # 時刻を得られない版は最新とみなし、後継の判定にも回収の対象にも使わない。
        return float("inf")


def _resolve(path: Path) -> Path:
    """比較用に実体パスへ解決する。解決できない場合は入力をそのまま返す。"""
    try:
        return path.resolve()
    except OSError:
        return path
