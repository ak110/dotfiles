"""install-claude.sh / install-claude.ps1 のテスト。

ローカル HTTP サーバーを起動して `.chezmoi-source/dot_claude/rules/` を配信し、
`$HOME` を差し替えた状態でスクリプトを実行して成果物を検証する。
ネットワーク依存なしで決定的に動く。
"""

import functools
import http.server
import pathlib
import shutil
import socketserver
import subprocess
import threading
import typing

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RULES_SRC = REPO_ROOT / ".chezmoi-source" / "dot_claude" / "rules" / "agent-basics"
INSTALL_SH = REPO_ROOT / "install-claude.sh"
INSTALL_PS1 = REPO_ROOT / "install-claude.ps1"


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


# 対象スクリプト種別: 実行可否を事前判定
def _runners() -> list:
    params: list = [pytest.param("sh", id="sh")]
    if shutil.which("pwsh"):
        params.append(pytest.param("ps1", id="ps1"))
    else:
        params.append(pytest.param("ps1", id="ps1", marks=pytest.mark.skip(reason="pwsh未インストール")))
    return params


def _run(kind: str, home: pathlib.Path, rules_url: str) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "DOTFILES_RULES_URL": rules_url,
    }
    cmd = ["bash", str(INSTALL_SH)] if kind == "sh" else ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(INSTALL_PS1)]
    return subprocess.run(cmd, env=env, check=True, capture_output=True, text=True)


@pytest.mark.parametrize("kind", _runners())
class TestInstallClaude:
    """install-claude.sh / .ps1 の動作確認。"""

    def test_add_then_noop_then_overwrite(self, kind: str, tmp_path: pathlib.Path, rules_url: str):
        """追加 → 変更なし → 上書き+バックアップのシナリオ。"""
        home = tmp_path / "home"
        home.mkdir()

        # 1回目: 追加（rules側の配布対象はagent.mdのみ。
        # その他の規約はagent-toolkitプラグインのスキルが担う）
        result = _run(kind, home, rules_url)
        rules_dir = home / ".claude" / "rules" / "agent-basics"
        assert (rules_dir / "agent.md").exists(), "agent.md が配置されていない"
        # スキルへ移行済みの旧ルールは配布されないこと
        for name in ["markdown.md", "python.md", "claude.md", "claude-rules.md", "typescript.md"]:
            assert not (rules_dir / name).exists(), f"{name} が配布されている（移行済みのはず）"
        assert "追加" in result.stdout

        # 2回目: 変更なし
        result = _run(kind, home, rules_url)
        assert "追加" not in result.stdout
        assert "上書き" not in result.stdout
        assert "変更なし" in result.stdout

        # 3回目: body を書き換えて再実行 → 上書き+バックアップ
        target = rules_dir / "agent.md"
        target.write_text("# 古い内容\n", encoding="utf-8")
        result = _run(kind, home, rules_url)
        assert "上書き" in result.stdout
        assert "agent.md" in result.stdout
        backups = list(home.glob(".claude/rules-backup/agent-basics-*"))
        assert len(backups) == 1, f"バックアップディレクトリが作成されていない: {backups}"
        assert (backups[0] / "agent.md").exists()
        # 上書き後は元のテンプレート内容と一致
        expected = (RULES_SRC / "agent.md").read_text(encoding="utf-8")
        assert target.read_text(encoding="utf-8") == expected

    def test_preserve_custom_frontmatter(self, kind: str, tmp_path: pathlib.Path, rules_url: str):
        """既存ファイルのカスタム frontmatter は維持され body のみ更新される。"""
        home = tmp_path / "home"
        home.mkdir()
        rules_dir = home / ".claude" / "rules" / "agent-basics"
        rules_dir.mkdir(parents=True)
        target = rules_dir / "agent.md"
        target.write_text(
            '---\npaths:\n  - "custom/path"\n---\n# 古い body\n',
            encoding="utf-8",
        )

        _run(kind, home, rules_url)

        result = target.read_text(encoding="utf-8")
        assert '"custom/path"' in result, "カスタム frontmatter が維持されていない"
        assert "# 古い body" not in result, "body が更新されていない"
        # テンプレートのbodyが入っている
        template_body = (RULES_SRC / "agent.md").read_text(encoding="utf-8")
        # テンプレートbodyの特徴的な行
        assert "# カスタム指示" in template_body
        assert "# カスタム指示" in result
