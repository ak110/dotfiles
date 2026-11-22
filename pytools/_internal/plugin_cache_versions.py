"""plugin cacheの版ディレクトリを導入順に並べ、後継版の導入から猶予期間を過ぎた版を選ぶ。

Claude CodeとCodexはどちらも`<cache>/<marketplace>/<name>/<version>/`へ版ごとに導入し、
セッションは起動時に解決した版の絶対パスを再開後も使い続ける。
後継版の導入時刻はその版が現行でなくなった時刻であり、そこから猶予期間を過ぎた版を回収の候補にする。
稼働中の参照の判定は、ホストごとの回収処理が行う。
"""

from collections.abc import Iterable
from pathlib import Path

# 後継版の導入から旧版を回収の候補にするまでの日数。
# 2026年11月19日の観測では、Codexのセッション記録（rollout）1万1千件の開始から最終書込までが最長3.2日だった。
GRACE_DAYS = 7


def expired(version_dirs: Iterable[Path], keep: set[Path], deadline: float, manifest: str) -> list[Path]:
    timed = sorted(((_installed_at(path, manifest), path) for path in version_dirs), key=lambda item: (item[0], item[1].name))
    result: list[Path] = []
    for (_, version_dir), (successor_installed_at, _) in zip(timed, timed[1:], strict=False):
        if _resolve(version_dir) not in keep and successor_installed_at <= deadline:
            result.append(version_dir)
    return result


def _installed_at(version_dir: Path, manifest: str) -> float:
    try:
        return (version_dir / manifest).stat().st_mtime
    except OSError:
        pass
    try:
        return version_dir.stat().st_mtime
    except OSError:
        return float("inf")


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path
