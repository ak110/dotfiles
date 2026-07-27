"""install-claude.sh / install-claude.ps1 のテスト。

ローカル HTTP サーバーを起動して `agent-toolkit/rules/` を配信し、
`$HOME` を差し替えた状態でスクリプトを実行して成果物を検証する。
ネットワーク依存なしで決定的に動く。

`claude` CLI 検出チェックをクリアするため、PATH に `claude` スタブを挿入して
呼び出し引数を環境変数 `CLAUDE_STUB_LOG` が指すファイルへ追記させる。
"""

import functools
import http.server
import os
import pathlib
import shutil
import socketserver
import stat
import subprocess
import threading
import typing

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent
RULES_SRC = REPO_ROOT / "agent-toolkit" / "rules"
INSTALL_SH = REPO_ROOT / "install-claude.sh"
INSTALL_PS1 = REPO_ROOT / "install-claude.ps1"

_CLAUDE_STUB = """#!/bin/sh
if [ -n "$CLAUDE_STUB_LOG" ]; then
    printf '%s\\n' "$*" >> "$CLAUDE_STUB_LOG"
fi
exit 0
"""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        del args, kwargs  # ログ抑止


@pytest.fixture(name="rules_url", scope="module")
def rules_url_fixture() -> typing.Iterator[str]:
    """ローカル HTTP サーバーを起動してルールディレクトリを配信する。"""
    handler = functools.partial(_QuietHandler, directory=str(RULES_SRC))

    class _Server(socketserver.TCPServer):
        allow_reuse_address = True

    with _Server(("127.0.0.1", 0), handler) as server:
        port = typing.cast(tuple[str, int], server.server_address)[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.shutdown()
            thread.join()


def _runners() -> list:
    params: list = [pytest.param("sh", id="sh")]
    if shutil.which("pwsh"):
        params.append(pytest.param("ps1", id="ps1"))
    else:
        params.append(pytest.param("ps1", id="ps1", marks=pytest.mark.skip(reason="pwsh未インストール")))
    return params


def _make_claude_stub(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """`claude` スタブを作成し、（binディレクトリ, ログパス）を返す。"""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "claude"
    stub.write_text(_CLAUDE_STUB, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log_path = tmp_path / "claude.log"
    log_path.touch()
    return bin_dir, log_path


def _run(
    kind: str,
    home: pathlib.Path,
    rules_url: str,
    *,
    stub_bin: pathlib.Path | None = None,
    stub_log: pathlib.Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    path_parts: list[str] = []
    if stub_bin is not None:
        path_parts.append(str(stub_bin))
    path_parts.extend(["/usr/bin", "/bin", "/usr/local/bin"])
    # pwsh 実行時は pwsh 本体の解決に必要なディレクトリも PATH に含める。
    if kind == "ps1":
        pwsh = shutil.which("pwsh")
        if pwsh:
            pwsh_dir = str(pathlib.Path(pwsh).parent)
            if pwsh_dir not in path_parts:
                path_parts.insert(1 if stub_bin is not None else 0, pwsh_dir)

    env = {
        "HOME": str(home),
        "PATH": os.pathsep.join(path_parts),
        "DOTFILES_RULES_URL": rules_url,
    }
    if stub_log is not None:
        env["CLAUDE_STUB_LOG"] = str(stub_log)

    cmd = ["bash", str(INSTALL_SH)] if kind == "sh" else ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(INSTALL_PS1)]
    return subprocess.run(cmd, env=env, check=check, capture_output=True, text=True)


@pytest.mark.parametrize("kind", _runners())
class TestInstallClaude:
    """install-claude.sh / .ps1 の動作確認。"""

    def test_deployment_cleanup_and_command_sequence(self, kind: str, tmp_path: pathlib.Path, rules_url: str):
        """配布・旧ディレクトリ削除・余分ファイル削除・claudeコマンド順序を1回の実行でまとめて検証する。

        `install-claude.sh`の配布先一括差し替え(mv)と旧agent-basics削除は独立したif分岐であり、
        両方の事前条件を同時に用意しても相互作用が無いことを確認済み（計画「### エージェント判断」参照）。
        """
        home = tmp_path / "home"
        home.mkdir()
        stub_bin, stub_log = _make_claude_stub(tmp_path)

        legacy_dir = home / ".claude" / "rules" / "agent-basics"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "01-agent.md").write_text("# 旧配布\n", encoding="utf-8")

        rules_dir = home / ".claude" / "rules" / "agent-toolkit"
        rules_dir.mkdir(parents=True)
        extra = rules_dir / "obsolete.md"
        extra.write_text("# 旧ファイル\n", encoding="utf-8")

        _run(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log)

        # 配布内容が配布元と完全一致する
        assert (rules_dir / "01-agent.md").exists()
        assert (rules_dir / "01-agent.md").read_text(encoding="utf-8") == (RULES_SRC / "01-agent.md").read_text(
            encoding="utf-8"
        )
        # 旧agent-basicsディレクトリが削除される
        assert not legacy_dir.exists(), "旧 agent-basics ディレクトリが削除されていない"
        # 配布元に存在しない余分ファイルが削除される
        assert not extra.exists(), "配布元に存在しないファイルが残っている"

        # agent-toolkit プラグイン関連の claude コマンドが想定順序で呼ばれる
        lines = [ln for ln in stub_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        expected_substrings = [
            "plugin marketplace add ak110/dotfiles --scope=user",
            "plugin marketplace update ak110-dotfiles",
            "plugin uninstall edit-guardrails@ak110-dotfiles",
            "plugin install agent-toolkit@ak110-dotfiles --scope=user",
            "plugin update agent-toolkit@ak110-dotfiles --scope=user",
        ]
        joined = "\n".join(lines)
        last_idx = -1
        for substr in expected_substrings:
            idx = joined.find(substr, last_idx + 1)
            assert idx > last_idx, f"未呼び出しまたは順序違反: {substr!r}\nlog={joined}"
            last_idx = idx

    def test_stage_dir_cleaned_on_failure(self, kind: str, tmp_path: pathlib.Path):
        """ダウンロード失敗時に既存の agent-toolkit が保持され、ステージ領域が残らない。"""
        home = tmp_path / "home"
        home.mkdir()
        stub_bin, stub_log = _make_claude_stub(tmp_path)

        # 既存環境を先に生成する
        rules_dir = home / ".claude" / "rules" / "agent-toolkit"
        rules_dir.mkdir(parents=True)
        sentinel = rules_dir / "01-agent.md"
        sentinel.write_text("# 既存内容\n", encoding="utf-8")

        # 無効なURLを与えてダウンロードを失敗させる
        bad_url = "http://127.0.0.1:1/does-not-exist"
        result = _run(kind, home, bad_url, stub_bin=stub_bin, stub_log=stub_log, check=False)
        assert result.returncode != 0

        assert sentinel.exists(), "失敗時に既存ファイルが失われた"
        assert sentinel.read_text(encoding="utf-8") == "# 既存内容\n"
        stage_root = home / ".claude" / "rules-stage"
        if stage_root.exists():
            remaining = list(stage_root.iterdir())
            assert not remaining, f"ステージ領域が残っている: {remaining}"


@pytest.mark.parametrize("kind", _runners())
def test_exits_when_claude_missing(kind: str, tmp_path: pathlib.Path, rules_url: str):
    """claude CLI 未検出なら非ゼロ終了し、配布先を生成しない。"""
    home = tmp_path / "home"
    home.mkdir()

    result = _run(kind, home, rules_url, stub_bin=None, stub_log=None, check=False)

    assert result.returncode != 0
    rules_dir = home / ".claude" / "rules" / "agent-toolkit"
    assert not rules_dir.exists(), "claude 未検出時に配布が行われた"
