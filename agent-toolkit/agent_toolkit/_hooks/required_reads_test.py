"""全文読解対象のパス解決を検証する。"""

import ntpath
import pathlib
import posixpath

from agent_toolkit._hooks import required_reads


def test_plugin_root_is_package_root() -> None:
    assert required_reads.plugin_root() == pathlib.Path(required_reads.__file__).resolve().parents[2]


def test_matches_document_with_posix_paths() -> None:
    root = "/opt/plugins/agent-toolkit/2.106.0"
    expected = posixpath.join(root, *required_reads.DOCUMENT_PARTS)

    assert required_reads.matches_document(expected, plugin_root=root, path_module=posixpath)
    assert not required_reads.matches_document(
        posixpath.join("/home/u/dotfiles/agent-toolkit", *required_reads.DOCUMENT_PARTS),
        plugin_root=root,
        path_module=posixpath,
    )


def test_matches_document_with_windows_paths() -> None:
    root = r"C:\Users\u\.claude\plugins\cache\ak110-dotfiles\agent-toolkit\2.106.0"
    expected = ntpath.join(root, *required_reads.DOCUMENT_PARTS)

    assert required_reads.matches_document(expected, plugin_root=root, path_module=ntpath)
    assert required_reads.matches_document(expected.upper(), plugin_root=root, path_module=ntpath)
    assert not required_reads.matches_document(
        ntpath.join(r"C:\Users\u\dotfiles\agent-toolkit", *required_reads.DOCUMENT_PARTS),
        plugin_root=root,
        path_module=ntpath,
    )


def test_default_arguments_match_only_running_plugin_root() -> None:
    expected = required_reads.document_path()
    worktree_copy = "/tmp/copy/skills/review-standards/references/judgment-details.md"

    assert required_reads.matches_document(expected)
    assert not required_reads.matches_document(worktree_copy)
