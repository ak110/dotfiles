"""statuslineの版数検査が、差分と版数とタグの組み合わせを正しく判定することを検証する。"""

import pathlib
import subprocess

import check_statusline_version
import pytest

_MANIFEST_TEMPLATE = '[package]\nname = "claude-statusline"\nversion = "{version}"\nedition = "2024"\n'


def _git(cwd: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _write(repo: pathlib.Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture(name="repo")
def _repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """版数0.1.0をmasterへ公開済みのoriginと、そのcloneを用意する。"""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "--initial-branch=master", str(origin))
    repo = tmp_path / "work"
    _git(tmp_path, "clone", str(origin), str(repo))
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "checkout", "-b", "master")
    _write(repo, "rust/claude-statusline/Cargo.toml", _MANIFEST_TEMPLATE.format(version="0.1.0"))
    _write(repo, "rust/claude-statusline/src/subagent.rs", 'const INPUT: &str = "before";\n')
    _write(repo, "README.md", "readme\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    _git(repo, "push", "origin", "master")
    _git(repo, "checkout", "-b", "develop")
    monkeypatch.delenv("BASE_SHA", raising=False)
    monkeypatch.delenv("CURRENT_SHA", raising=False)
    return repo


def _check(repo: pathlib.Path, *, metadata_ok: bool = True) -> str | None:
    return check_statusline_version.check(repo, metadata_command=["true"] if metadata_ok else ["false"])


def test_passes_without_statusline_changes(repo: pathlib.Path) -> None:
    """statusline配下に差分が無ければ版数を問わない。"""
    _write(repo, "README.md", "changed\n")

    assert _check(repo) is None


def test_test_input_only_change_requires_version_bump_until_bumped(repo: pathlib.Path) -> None:
    """テスト入力だけの未commitの変更でも版数更新を求め、更新すれば通す。"""
    _write(repo, "rust/claude-statusline/src/subagent.rs", 'const INPUT: &str = "after";\n')

    assert _check(repo) == "statuslineの変更にはCargo.tomlの版数更新が必要である。"

    _write(repo, "rust/claude-statusline/Cargo.toml", _MANIFEST_TEMPLATE.format(version="0.1.1"))
    assert _check(repo) is None


def test_untracked_file_counts_as_change(repo: pathlib.Path) -> None:
    """statusline配下へ未追跡のファイルを加えた場合も差分として扱う。"""
    _write(repo, "rust/claude-statusline/src/new.rs", "// new\n")

    assert _check(repo) is not None


def test_committed_change_is_judged_against_current_sha(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CIと同じく`CURRENT_SHA`を与えた場合はそのcommitまでの差分で判定する。"""
    _write(repo, "rust/claude-statusline/Cargo.lock", "# lock\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "lock only")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    monkeypatch.setenv("CURRENT_SHA", head)

    assert _check(repo) == "statuslineの変更にはCargo.tomlの版数更新が必要である。"


def test_existing_tag_for_new_version_fails(repo: pathlib.Path) -> None:
    """更新後の版数のタグがoriginに既にあれば失敗とする。"""
    _git(repo, "tag", "statusline-v0.1.1")
    _git(repo, "push", "origin", "statusline-v0.1.1")
    _write(repo, "rust/claude-statusline/Cargo.toml", _MANIFEST_TEMPLATE.format(version="0.1.1"))

    assert _check(repo) == "statuslineの版数に対応する既存タグがある: statusline-v0.1.1"


def test_cargo_metadata_failure_is_not_treated_as_success(repo: pathlib.Path) -> None:
    """Cargo.lockとの不整合などでcargo metadataが失敗した場合は前提の失敗として扱う。"""
    _write(repo, "rust/claude-statusline/Cargo.toml", _MANIFEST_TEMPLATE.format(version="0.1.1"))

    with pytest.raises(check_statusline_version.CheckError, match="cargo metadata"):
        _check(repo, metadata_ok=False)


def test_unreachable_origin_is_not_treated_as_success(repo: pathlib.Path) -> None:
    """originへ到達できない場合は検査を黙って通過させない。"""
    _git(repo, "remote", "set-url", "origin", str(repo.parent / "missing.git"))
    _write(repo, "rust/claude-statusline/src/subagent.rs", 'const INPUT: &str = "after";\n')

    with pytest.raises(check_statusline_version.CheckError, match="git fetch"):
        _check(repo)
