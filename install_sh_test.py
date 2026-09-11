"""install.sh のテスト。

隔離 `$HOME` に現在のリポジトリを複製して install.sh を実行し、chezmoi による
デプロイが行われることを検証する。隔離の対象は `$HOME` と `PATH` であり、外部到達性は
遮断しない。install.sh 配下の `uv tool install` は PyPI と python-build-standalone へ
実際に到達するため、外部到達性設定（プロキシ変数）は親環境から引き継ぐ。

到達先を差し替えられる分岐は、次の手段で実 GitHub・実 HTTP への依存を除く。

- git clone 分岐は事前に `$FAKE_HOME/dotfiles` を用意して回避
- chezmoi ダウンロード分岐はシステムの chezmoi バイナリを `$FAKE_HOME/.local/bin/`
  にコピーして回避
- Claude Codeとnpmの導入分岐は成功する代替実行ファイルを`$FAKE_HOME/.local/bin/`に配置して回避
- Codex CLIの導入分岐は複製したリポジトリ内の導入モジュールをテスト用実装へ置き換えて回避
"""

import http.server
import os
import pathlib
import shutil
import socketserver
import subprocess
import threading
import typing

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent
INSTALL_SH = REPO_ROOT / "install.sh"


@pytest.mark.skipif(shutil.which("chezmoi") is None, reason="chezmoi未インストール")
@pytest.mark.timeout(300)
def test_install_sh_deploys_rules(tmp_path: pathlib.Path):
    """install.sh が chezmoi でルールを ~/.claude/rules/ に配置する。"""
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    # 1. リポジトリを fake_home/dotfiles に複製（git clone 分岐を回避）
    fake_dotfiles = fake_home / "dotfiles"
    _copy_repo(REPO_ROOT, fake_dotfiles)
    _disable_codex_cli_setup(fake_dotfiles)

    # 2. システムの chezmoi と uv を fake_home/.local/bin に配置（ダウンロード分岐を回避）。
    # uv は run_after_post-apply.sh.tmpl が `uv tool install` で dotfiles-post-apply を導入する際に必要だが、
    # 実インストール先（例: ~/.local/bin）が /usr/bin・/bin・/usr/local/bin のいずれにも無い環境では
    # 後続のPATHへ解決されず、pytools導入とルール配布が無言でスキップされる。
    chezmoi_bin = shutil.which("chezmoi")
    assert chezmoi_bin is not None
    uv_bin = shutil.which("uv")
    assert uv_bin is not None
    local_bin = fake_home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    shutil.copy2(chezmoi_bin, local_bin / "chezmoi")
    shutil.copy2(uv_bin, local_bin / "uv")
    _write_fake_cli(local_bin / "claude")
    _write_fake_cli(local_bin / "codex")
    _write_fake_npm(local_bin / "npm")

    # 3. tmuxプラグインのclone元をローカルミラーへ差し替える（実GitHub依存を回避）。
    # 戻り値のコミットSHAは末尾のアサーションで実際のclone結果と照合する。
    mirror_base = tmp_path / "git-mirrors"
    tpm_sha = _make_git_mirror(mirror_base, "tmux-plugins/tpm.git", tag=None)
    catppuccin_sha = _make_git_mirror(mirror_base, "catppuccin/tmux.git", tag="v2.3.0")
    tmux_cpu_sha = _make_git_mirror(mirror_base, "tmux-plugins/tmux-cpu.git", tag=None)

    # 4. statuslineバイナリのダウンロード元をローカルHTTPサーバーへ差し替える（実HTTP依存を回避）。
    class _Server(socketserver.TCPServer):
        allow_reuse_address = True

    with _Server(("127.0.0.1", 0), _QuietHandler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            env = {
                "HOME": str(fake_home),
                "PATH": f"{local_bin}:/usr/bin:/bin:/usr/local/bin",
                "LANG": "C.UTF-8",
                "DOTFILES_TMUX_PLUGIN_ORIGIN_BASE": f"file://{mirror_base}",
                "DOTFILES_STATUSLINE_DOWNLOAD_URL": f"http://127.0.0.1:{port}/binary",
                **_external_reachability_env(),
            }
            completed = subprocess.run(
                ["bash", str(INSTALL_SH)],
                env=env,
                check=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )
        finally:
            httpd.shutdown()
            thread.join()

    # install.shは配下の処理の失敗を縮退して終了コード0で終わるため、
    # 後段のassertが失敗した場合に原因の段を特定できるよう実行時の出力を保存する。
    stdout_log = tmp_path / "install-sh.stdout.log"
    stderr_log = tmp_path / "install-sh.stderr.log"
    stdout_log.write_text(completed.stdout, encoding="utf-8")
    stderr_log.write_text(completed.stderr, encoding="utf-8")

    # 5. ルールファイルがデプロイされていること。
    # rules側の配布対象は生成一覧を正本とし、POSIX版とWindows版の完全一致を検査する。
    # その他の規約はagent-toolkitプラグインのスキルが担う。
    # 代表として01-agent.mdの存在のみを検証する（ファイル一覧の一致は install_script_ssot_test.py が担う）。
    rules_dir = fake_home / ".claude" / "rules" / "agent-toolkit"
    assert (rules_dir / "01-agent.md").exists(), (
        f"01-agent.md が chezmoi でデプロイされていない（install.shの出力: {stdout_log} / {stderr_log}）"
    )

    # tmuxプラグイン3件（tpm・catppuccin/tmux・tmux-cpu）が正しくcloneされていることを検証する。
    plugins_dir = fake_home / ".tmux" / "plugins"
    assert (plugins_dir / "tpm").is_dir(), "tpmが導入されていない"
    assert _rev_parse_head(plugins_dir / "tpm") == tpm_sha, "tpmのclone内容がミラーと一致しない"
    assert (plugins_dir / "tmux").is_dir(), "catppuccin/tmuxが導入されていない"
    assert _rev_parse_head(plugins_dir / "tmux") == catppuccin_sha, "catppuccin/tmuxのタグ指定clone内容がミラーと一致しない"
    assert (plugins_dir / "tmux-cpu").is_dir(), "tmux-cpuが導入されていない"
    assert _rev_parse_head(plugins_dir / "tmux-cpu") == tmux_cpu_sha, "tmux-cpuのclone内容がミラーと一致しない"
    assert (fake_home / ".local" / "bin" / "claude-statusline").read_bytes() == b"FAKE_STATUSLINE_BINARY", (
        "claude-statuslineバイナリが配置されていない"
    )


def _copy_repo(src: pathlib.Path, dst: pathlib.Path) -> None:
    """リポジトリを複製。.venv や巨大ディレクトリは除外して高速化。"""

    # .git は chezmoi init が内部で使うわけではないので不要。
    # .venv / node_modules などテスト無関係な巨大ディレクトリは除外する。
    def _ignore(_dir: str, names: list[str]) -> list[str]:
        del _dir  # noqa
        return [n for n in names if n in {".venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", ".git"}]

    shutil.copytree(src, dst, ignore=_ignore, symlinks=True)


def _disable_codex_cli_setup(repo: pathlib.Path) -> None:
    """隔離インストールで外部取得を避けるためCodex CLI導入をテスト用実装へ置き換える。"""
    module = repo / "pytools" / "_internal" / "setup_codex_cli.py"
    module.write_text(
        '"""install.sh統合テスト用のCodex CLI導入スタブ。"""\n\n\n'
        "def run() -> bool:\n"
        '    """外部取得を行わず未変更として返す。"""\n'
        "    return False\n",
        encoding="utf-8",
    )


def _write_fake_cli(path: pathlib.Path) -> None:
    """外部取得を避けるため、常に成功するCLI代替実行ファイルを配置する。"""
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def _write_fake_npm(path: pathlib.Path) -> None:
    """Codexのglobal導入先を隔離HOMEへ向けるnpm代替実行ファイルを配置する。"""
    path.write_text(
        '#!/bin/sh\nif [ "$1" = "prefix" ]; then\n    printf "%s\\n" "$HOME/.local"\nfi\nexit 0\n',
        encoding="utf-8",
    )
    path.chmod(0o755)


def _make_git_mirror(base: pathlib.Path, rel_path: str, *, tag: str | None) -> str:
    """`base/rel_path`へ最小構成のgitリポジトリを作成し、コミットSHAを返す。

    戻り値は`setup_tmux_plugins`経由でcloneされた配置先が同一コミットを指すことを
    検証するための照合値として使う（ディレクトリの存在確認だけでは
    clone失敗を見逃すため、実際のコミット内容一致まで確認する）。
    """
    repo = base / rel_path
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "--initial-branch=master", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
    (repo / "README.md").write_text("stub\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "stub"], check=True, capture_output=True)
    if tag:
        subprocess.run(["git", "-C", str(repo), "tag", tag], check=True)
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _external_reachability_env() -> dict[str, str]:
    """親環境にある外部到達性設定だけを取り出して返す。

    install.sh配下の`uv tool install`はPyPIとpython-build-standaloneへ実際に到達するため、
    プロキシ経由でのみ外部へ到達するホストでは当該設定なしにインタープリターを取得できない。
    存在しない変数は追加しないため、直接到達できるホストへ渡す環境は変わらない。
    HOMEやXDG_*を含む親環境全体は引き継がない（隔離の漏れが実HOMEへの書き込みとして現れるため）。
    """
    keys = ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")
    return {key: os.environ[key] for key in keys if key in os.environ}


def _rev_parse_head(repo: pathlib.Path) -> str:
    """`repo`のHEADコミットSHAを返す（clone先が期待コミットと一致するかの照合に使う）。"""
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


class _QuietHandler(http.server.BaseHTTPRequestHandler):
    """statuslineバイナリ代替を返す最小HTTPハンドラ。ログは抑止する。"""

    def do_GET(self) -> None:  # noqa: N802 -- http.server既定APIの命名規約
        payload = b"FAKE_STATUSLINE_BINARY"
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        del args, kwargs
