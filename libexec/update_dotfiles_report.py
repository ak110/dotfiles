"""繰り返す更新失敗を、利用可能なWIキューへ重複なく記録する。"""

import hashlib
import html
import json
import os
import pathlib
import re
import shutil
import subprocess

import filelock
from agent_toolkit._atk.wi.constants import WI_STATES
from agent_toolkit._common import private_notes, process_tree, wi_layout
from agent_toolkit._common.diagnostic_redaction import redact_secrets


def bootstrap_failures(directory: pathlib.Path) -> list[tuple[str, str]]:
    """外側の段が渡した診断を検証して取り込み、所有する一時記録だけを回収する。"""
    failures = []
    for line in os.environ.pop("UPDATE_DOTFILES_BOOTSTRAP_FAILURES", "").splitlines():
        title, separator, detail = line.partition(": ")
        if separator and len(line) <= 4096:
            failures.append((title, detail))
    raw_path = os.environ.pop("UPDATE_DOTFILES_BOOTSTRAP_REPORT", "")
    if not raw_path:
        return failures
    path = pathlib.Path(raw_path)
    try:
        if path.parent.resolve() != directory.resolve() or not re.fullmatch(r"update-bootstrap-\d+-\d+\.json", path.name):
            return failures
        if path.is_symlink():
            return failures
        with path.open(encoding="utf-8-sig") as source:
            body = source.read(65537)
        if len(body) > 65536:
            return failures
        payload = json.loads(body)
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and all(isinstance(item.get(key), str) for key in ("title", "detail")):
                    failures.append((item["title"][:128], item["detail"][:4096]))
        path.unlink()
    except OSError, ValueError:
        pass
    return failures


def report(failures: list[tuple[str, str]], state: pathlib.Path, root: pathlib.Path) -> None:
    """同じ失敗が3回続いた場合だけ起票する。起票不能のホストでは更新を妨げない。"""
    try:
        state.parent.mkdir(parents=True, exist_ok=True)
        with filelock.FileLock(str(state.with_suffix(".lock")), timeout=1):
            previous = json.loads(state.read_text(encoding="utf-8")) if state.exists() else {}
            if not isinstance(previous, dict):
                previous = {}
            current = {}
            atk = shutil.which("atk")
            for title, detail in failures:
                title = re.sub(r"\s+", " ", redact_secrets(title))[:160]
                normalized = re.sub(r"\b\d+\b", "<number>", redact_secrets(detail))
                key = hashlib.sha256(f"{title}\n{normalized}".encode()).hexdigest()
                old = previous.get(key, {})
                count = old.get("count", 0) if isinstance(old, dict) else 0
                count = count if isinstance(count, int) and not isinstance(count, bool) else 0
                saved = bool(old.get("saved")) if isinstance(old, dict) else False
                current[key] = {"count": count + 1, "saved": saved}
                if count < 2 or atk is None:
                    continue
                notes = private_notes.default_private_notes()
                if not notes.is_dir():
                    continue
                if _pending(notes, key):
                    current[key]["saved"] = True
                    continue
                body = state.parent / f"update-failure-{key}.md"
                body.write_text(_body(key, title, normalized), encoding="utf-8")
                try:
                    result = process_tree.run_with_timeout(
                        [atk, "wi", "add", "--body-file", str(body), "--target-repo", str(root), "--source", "agent"],
                        check=False,
                        capture_output=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=120,
                    )
                    current[key]["saved"] = result.returncode == 0
                finally:
                    body.unlink(missing_ok=True)
            temporary = state.with_suffix(".new")
            temporary.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
            temporary.replace(state)
    except process_tree.ReclaimError:
        raise
    except OSError, ValueError, RuntimeError, subprocess.SubprocessError, filelock.Timeout:
        # 更新の結果表示は呼出元が行う。キューと状態領域の障害では更新を止めない。
        pass


def _pending(notes: pathlib.Path, key: str) -> bool:
    for path in wi_layout.iter_entry_paths(notes, legacy_states=WI_STATES):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            continue
        header = text[4:].split("\n---", maxsplit=1)[0]
        # 自動起票が書く単純なスカラーだけを読む。更新の起動前提へYAMLの依存を追加しないためである。
        if re.search(rf"""(?m)^update_dotfiles_failure_key: ['"]?{key}['"]?\s*$""", header) and re.search(
            r"""(?m)^state: ['"]?(?:inbox|processing|hold)['"]?\s*$""", header
        ):
            return True
    return False


def _body(key: str, title: str, detail: str) -> str:
    # 段名には外部のディレクトリ名も含まれるため、Markdownのテキストとしてエスケープする。
    title = re.sub(r"([\\`*_{}\[\]()#+.!|<>-])", r"\\\1", html.escape(title))
    return (
        f"---\nupdate_dotfiles_failure_key: {key}\n---\n\n"
        f"# update-dotfilesの反復する失敗を是正する: {title}\n\n"
        "同じ段と診断の失敗を3回続けて観測した。失敗で更新全体を止めず、原因を調べて是正する。\n\n"
        "## 反映内容と反映先\n\n"
        "dotfilesの更新処理で下記の診断が生じる原因を特定し、原因に対応する実装と試験を修正する。\n\n"
        "## 調査結果と判断根拠\n\n"
        f"観測した段: {title}\n\n診断（秘匿値と変動する数値を正規化）:\n\n    "
        + detail.replace("\n", "\n    ")
        + "\n\n## 完成条件\n\n合成入力で失敗を再現し、修正後に当該段と後続の更新が完了する試験を通す。\n"
    )
