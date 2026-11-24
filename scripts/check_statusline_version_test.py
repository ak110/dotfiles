import os
import pathlib
import subprocess
import sys

import check_statusline_version
import pytest
import yaml

_MANIFEST_TEMPLATE = '[package]\nname = "claude-statusline"\nversion = "{version}"\nedition = "2024"\n'


def _git(cwd: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _write(repo: pathlib.Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture(name="repo")
def _repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
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
    _write(repo, "README.md", "changed\n")

    assert _check(repo) is None


def test_test_input_only_change_requires_version_bump_until_bumped(repo: pathlib.Path) -> None:
    _write(repo, "rust/claude-statusline/src/subagent.rs", 'const INPUT: &str = "after";\n')

    assert _check(repo) == "statuslineの変更にはCargo.tomlの版数更新が必要である。"

    _write(repo, "rust/claude-statusline/Cargo.toml", _MANIFEST_TEMPLATE.format(version="0.1.1"))
    assert _check(repo) is None


def test_untracked_file_counts_as_change(repo: pathlib.Path) -> None:
    _write(repo, "rust/claude-statusline/src/new.rs", "// new\n")

    assert _check(repo) == "statuslineの変更にはCargo.tomlの版数更新が必要である。"


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

    assert _check(repo) == (
        "statuslineの版数に対応する既存タグがある: statusline-v0.1.1。"
        "rust/claude-statusline/Cargo.tomlのversionを未公開の版へ上げ、"
        "同ディレクトリでcargo checkを実行してCargo.lockを更新してから再検査してください。"
    )


def test_cargo_metadata_failure_is_not_treated_as_success(repo: pathlib.Path) -> None:
    """Cargo.lockとの不整合などでcargo metadataが失敗した場合は前提の失敗として扱う。"""
    _write(repo, "rust/claude-statusline/Cargo.toml", _MANIFEST_TEMPLATE.format(version="0.1.1"))

    with pytest.raises(check_statusline_version.CheckError, match="cargo metadata"):
        _check(repo, metadata_ok=False)


def test_unreachable_origin_is_not_treated_as_success(repo: pathlib.Path) -> None:
    _git(repo, "remote", "set-url", "origin", str(repo.parent / "missing.git"))
    _write(repo, "rust/claude-statusline/src/subagent.rs", 'const INPUT: &str = "after";\n')

    with pytest.raises(check_statusline_version.CheckError, match="git fetch"):
        _check(repo)


def _commit(repo: pathlib.Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _push_tag(repo: pathlib.Path, tag: str, revision: str) -> None:
    _git(repo, "tag", tag, revision)
    _git(repo, "push", "origin", f"refs/tags/{tag}")


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        pytest.param("README.md", {"changed": "false", "version": "", "tag": ""}, id="without-statusline-change"),
        pytest.param(
            "rust/claude-statusline/src/subagent.rs",
            {"changed": "true", "version": "0.1.0", "tag": "statusline-v0.1.0"},
            id="with-statusline-change",
        ),
    ],
)
def test_release_prepare_compares_with_first_parent(repo: pathlib.Path, change: str, expected: dict[str, str]) -> None:
    _write(repo, change, "changed\n")
    head = _commit(repo, "change")

    assert check_statusline_version.release_prepare(repo, head, metadata_command=["true"]) == (None, expected)


@pytest.mark.parametrize(
    ("tag_on_head", "expected_failure"),
    [
        pytest.param(True, None, id="same-commit"),
        pytest.param(
            False,
            "既存タグが別のcommitを指している: statusline-v0.1.1。タグを上書きせず、"
            "git ls-remote originと公開対象commitの情報を添えてリリース担当者へ報告してください。",
            id="other-commit",
        ),
    ],
)
def test_release_modes_accept_only_tag_on_current_commit(
    repo: pathlib.Path, tag_on_head: bool, expected_failure: str | None
) -> None:
    """Releaseの再実行で作成済みのタグは受け入れ、別のcommitを指すタグは失敗とする。"""
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    _write(repo, "rust/claude-statusline/Cargo.toml", _MANIFEST_TEMPLATE.format(version="0.1.1"))
    head = _commit(repo, "bump")
    _push_tag(repo, "statusline-v0.1.1", head if tag_on_head else base)

    prepare_failure, _ = check_statusline_version.release_prepare(repo, head, metadata_command=["true"])
    tag_failure, tag_outputs = check_statusline_version.release_tag_state(repo, head)

    assert prepare_failure == expected_failure
    assert tag_failure == expected_failure
    assert tag_outputs == {"tag_exists": "true"}


def test_release_tag_state_reports_missing_tag(repo: pathlib.Path) -> None:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    assert check_statusline_version.release_tag_state(repo, head) == (None, {"tag_exists": "false"})


def test_release_tag_on_non_commit_object_is_a_precondition_failure(
    repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(repo, "rust/claude-statusline/Cargo.toml", _MANIFEST_TEMPLATE.format(version="0.1.1"))
    head = _commit(repo, "bump")
    tree = subprocess.run(["git", "write-tree"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    _push_tag(repo, "statusline-v0.1.1", tree)

    with pytest.raises(check_statusline_version.CheckError, match="タグstatusline-v0.1.1の照会"):
        check_statusline_version.release_prepare(repo, head, metadata_command=["true"])
    with pytest.raises(check_statusline_version.CheckError, match="タグstatusline-v0.1.1の照会"):
        check_statusline_version.release_tag_state(repo, head)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("CURRENT_SHA", head)
    assert check_statusline_version.main(["--release-tag-state", "--github-output", str(repo.parent / "out")]) == 2
    assert "タグstatusline-v0.1.1の照会" in capsys.readouterr().err


def test_release_tag_lookup_failure_is_not_treated_as_missing_tag(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """タグ照会がGitの障害で失敗した場合は、タグ不在として扱わず前提の失敗とする。"""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    original = check_statusline_version._git  # pylint: disable=protected-access

    def _failing_lookup(repo_root: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
        if args[:3] == ("rev-parse", "--verify", "--quiet"):
            return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: couldn't read .git/packed-refs")
        return original(repo_root, *args)

    monkeypatch.setattr(check_statusline_version, "_git", _failing_lookup)
    with pytest.raises(check_statusline_version.CheckError, match="終了コード128"):
        check_statusline_version.release_tag_state(repo, head)


def test_release_main_appends_outputs_and_requires_current_sha(
    repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """release用の指定は結果を`--github-output`へ追記し、`CURRENT_SHA`が無ければ前提の失敗とする。"""
    monkeypatch.chdir(repo)
    output = repo.parent / "github-output"
    output.write_text("existing=1\n", encoding="utf-8")

    assert check_statusline_version.main(["--release-tag-state", "--github-output", str(output)]) == 2
    assert "CURRENT_SHA" in capsys.readouterr().err

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    monkeypatch.setenv("CURRENT_SHA", head)
    assert check_statusline_version.main(["--release-tag-state", "--github-output", str(output)]) == 0
    assert output.read_text(encoding="utf-8") == "existing=1\ntag_exists=false\n"


_GH_DOUBLE = r"""
gh() {
  if [[ "$1 $2" == "api graphql" ]]; then
    printf '{"data":{"repository":{"latestRelease":{"tagName":"%s"}}}}\n' "$(cat "$RELEASES/latest")"
    return
  fi

  [[ "$1" == release ]] || return 64
  local action="$2" tag="$3" asset payload
  shift 3
  case "$action" in
    view) [[ -f "$RELEASES/$tag" && "$*" == "--json body --jq .body" ]] && cat "$RELEASES/notes/$tag";;
    edit)
      [[ -f "$RELEASES/$tag" && "$1" == --notes ]] || return 64
      printf '%s\n' "$2" > "$RELEASES/notes/$tag"
      ;;
    create|upload)
      local -a assets=()
      local latest= clobber=false notes=
      for asset in "$@"; do
        case "$asset" in
          --latest=*) latest="${asset#--latest=}";;
          --clobber) clobber=true;;
          --notes=*) notes="${asset#--notes=}";;
          --*) ;;
          *)
            [[ -f "$asset" ]] || return 68
            payload=$(cat -- "$asset")
            [[ "$payload" == "$EXPECTED_SHA:${asset##*/}" ]] || return 69
            assets+=("$payload")
            ;;
        esac
      done
      if [[ "$action" == create ]]; then
        [[ ! -e "$RELEASES/$tag" && "$latest" =~ ^(true|false)$ ]] || return 65
        git ls-remote --exit-code --refs origin "refs/tags/$tag" >/dev/null
      else
        [[ -f "$RELEASES/$tag" && "$clobber" == true ]] || return 66
      fi
      if [[ "$FAIL_UPLOAD" == true ]]; then
        echo "asset upload failed" >&2
        return 67
      fi
      printf '%s\n' "${assets[@]}" > "$RELEASES/$tag"
      if [[ "$action" == create ]]; then
        printf '%s\n' "$notes" > "$RELEASES/notes/$tag"
      fi
      if [[ "$latest" == true ]]; then
        printf '%s\n' "$tag" > "$RELEASES/latest"
      fi
      ;;
    *) return 64;;
  esac
}
"""


@pytest.mark.skipif(sys.platform == "win32", reason="公開jobと同じLinux上のBash・jqを使う")
@pytest.mark.parametrize("older_arrives_late", [False, True], ids=["merge-order", "older-arrives-late"])
def test_consecutive_statusline_releases(repo: pathlib.Path, older_arrives_late: bool) -> None:
    workflow_dir = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows"
    workflow = yaml.safe_load((workflow_dir / "release-statusline.yaml").read_text(encoding="utf-8"))
    ci = yaml.safe_load((workflow_dir / "ci.yaml").read_text(encoding="utf-8"))
    release = workflow["jobs"]["release"]
    publish = release["steps"][-1]
    assert release["env"]["HEAD_SHA"] == "${{ needs.gate.outputs.head_sha }}"
    assert release["env"]["TAG"] == "${{ needs.prepare.outputs.tag }}"
    assert publish["env"]["TAG_EXISTS"] == "${{ steps.tag-state.outputs.tag_exists }}"
    download = next(step["with"] for step in release["steps"] if step.get("uses", "").startswith("actions/download-artifact@"))
    assert download["merge-multiple"] is True
    releases = repo.parent / "releases"
    releases.mkdir()
    notes_dir = releases / "notes"
    notes_dir.mkdir()
    (releases / "latest").touch()
    versions = ("0.1.9", "0.1.10")
    assets = [
        "claude-statusline-x86_64-unknown-linux-gnu",
        "claude-statusline-x86_64-pc-windows-msvc.exe",
    ]
    assert {entry["asset"] for entry in ci["jobs"]["statusline-build"]["strategy"]["matrix"]["include"]} == set(assets)
    owner = ci["jobs"]["common-ci-owner"]
    selector = next(step for step in owner["steps"] if step.get("id") == "statusline-release")
    assert selector["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/master'"
    assert owner["outputs"]["statusline_release"] == "${{ steps.statusline-release.outputs.changed }}"

    def check_selection(expected: bool) -> None:
        output = repo.parent / "selection"
        subprocess.run(
            ["bash", "-c", selector["run"]], cwd=repo, env={**os.environ, "GITHUB_OUTPUT": str(output)}, check=True, timeout=10
        )
        assert output.read_text(encoding="utf-8").splitlines()[-1] == f"changed={str(expected).lower()}"

    commits = {}
    for version in versions:
        _write(repo, "rust/claude-statusline/Cargo.toml", _MANIFEST_TEMPLATE.format(version=version))
        commits[version] = _commit(repo, version)
        check_selection(True)
    _write(repo, "README.md", "次のpushではstatuslineを変更しない。\n")
    _commit(repo, "unrelated")
    check_selection(False)

    def run(version: str, *, missing_asset: bool = False, fail_upload: bool = False) -> None:
        sha = commits[version]
        tag = f"statusline-v{version}"
        failure, prepared = check_statusline_version.release_prepare(repo, sha, metadata_command=["true"])
        assert (failure, prepared) == (None, {"changed": "true", "version": version, "tag": tag})
        failure, tag_state = check_statusline_version.release_tag_state(repo, sha)
        assert failure is None
        for asset in assets[:1] if missing_asset else assets:
            _write(repo, f"{download['path']}/{asset}", f"{sha}:{asset}\n")
        result = subprocess.run(
            ["bash", "-c", _GH_DOUBLE + publish["run"]],
            cwd=repo,
            env={
                **os.environ,
                "HEAD_SHA": sha,
                "EXPECTED_SHA": sha,
                "TAG": prepared["tag"],
                "TAG_EXISTS": tag_state["tag_exists"],
                "RELEASES": str(releases),
                "FAIL_UPLOAD": str(fail_upload).lower(),
                "GITHUB_REPOSITORY": "example/dotfiles",
                "GITHUB_REPOSITORY_OWNER": "example",
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if missing_asset:
            assert result.returncode == 1 and assets[1] in result.stderr
            assert not (releases / tag).exists()
            assert subprocess.check_output(["git", "ls-remote", "--refs", "origin", f"refs/tags/{tag}"], cwd=repo) == b""
            return
        assert result.returncode == (67 if fail_upload else 0), result.stderr
        if fail_upload:
            assert "asset upload failed" in result.stderr
            assert not (releases / tag).exists()
        else:
            published = (releases / tag).read_text(encoding="utf-8").splitlines()
            assert sorted(published) == sorted(f"{sha}:{asset}" for asset in assets)
            notes = (notes_dir / tag).read_text(encoding="utf-8")
            assert notes.count("## ビルド来歴の検証") == 1
            assert "ak110/dotfiles/.github/workflows/ci.yaml" in notes
            assert "gh auth login" in notes and "GH_TOKEN" in notes
            assert "--source-digest" in notes and "https://github.com/ak110/dotfiles/attestations" in notes
        remote_tag = subprocess.check_output(["git", "ls-remote", "--refs", "origin", f"refs/tags/{tag}"], cwd=repo, text=True)
        assert remote_tag.strip() == f"{sha}\trefs/tags/{tag}"

    older, newer = versions
    run(older, missing_asset=True)
    if older_arrives_late:
        run(newer)
        run(older, fail_upload=True)
        assert (releases / "latest").read_text(encoding="utf-8").strip() == f"statusline-v{newer}"
        run(older)
    else:
        run(older)
        assert (releases / "latest").read_text(encoding="utf-8").strip() == f"statusline-v{older}"
        run(newer)
    assert (releases / "latest").read_text(encoding="utf-8").strip() == f"statusline-v{newer}"
    partial_release = releases / f"statusline-v{older}"
    partial_release.write_text(f"{commits[older]}:{assets[0]}\n", encoding="utf-8")
    existing_notes = notes_dir / partial_release.name
    existing_notes.write_text("既存の説明\n", encoding="utf-8")
    run(older)
    supplemented = existing_notes.read_text(encoding="utf-8")
    assert supplemented.startswith("既存の説明\n\n")
    run(older)
    assert existing_notes.read_text(encoding="utf-8") == supplemented
    assert (releases / "latest").read_text(encoding="utf-8").strip() == f"statusline-v{newer}"
    assert {path.name for path in releases.iterdir()} == {
        "latest",
        "notes",
        *(f"statusline-v{version}" for version in versions),
    }
