import json
import pathlib

import pytest

from agent_toolkit._atk.wi import repo as awi_repo
from agent_toolkit._atk.wi.layout import entry_path
from agent_toolkit._testing.config_support import _isolate_platformdirs, _write_config
from agent_toolkit._testing.serve_app_support import _fixed_path, _patch_single_add_operations, _saved_files, _serve_app
from agent_toolkit._testing.serve_support import _run_node_ui

__all__ = ["_isolate_platformdirs"]

_REGISTERED = "github.com/example/branched"
_PLAIN = "github.com/example/plain"


@pytest.fixture(name="registered_repository")
def _registered_repository_fixture(tmp_path: pathlib.Path) -> str:
    _write_config(
        tmp_path,
        {
            "repositories": json.dumps(
                {_REGISTERED: {"edit": "allow", "branches": ["maint", "develop"], "default_branch": "develop"}}
            )
        },
    )
    return _REGISTERED


def _write_entry(root: pathlib.Path, filename: str, repo: str, *, branch: str | None = None) -> None:
    branch_line = f"target_branch: {branch}\n" if branch is not None else ""
    _fixed_path(root, filename).write_text(
        f"---\nstate: inbox\ntype: awi\ntarget_repo: {repo}\n{branch_line}---\n\n本文{filename}\n", encoding="utf-8"
    )


async def _entries(client, query: str = "") -> dict[str, dict[str, object]]:  # noqa: ANN001
    response = await client.get(f"/api/entries?status=inbox{query}")
    assert response.status_code == 200
    return {item["filename"]: item for item in (await response.get_json())["entries"]}


@pytest.mark.asyncio
async def test_list_shows_effective_branch_only_for_registered_repository(
    tmp_path: pathlib.Path, registered_repository: str
) -> None:
    notes = tmp_path / "notes"
    _write_entry(notes, "stored.md", registered_repository, branch="maint")
    _write_entry(notes, "legacy.md", registered_repository)
    _write_entry(notes, "plain.md", _PLAIN)
    client = _serve_app(notes).test_client()

    entries = await _entries(client)

    assert entries["stored.md"]["target_branch"] == "maint"
    assert entries["legacy.md"]["target_branch"] == "develop"
    assert "target_branch" not in entries["plain.md"]
    detail = await (await client.get("/api/entries/inbox/legacy.md")).get_json()
    assert detail["entry"]["target_branch"] == "develop"
    plain_detail = await (await client.get("/api/entries/inbox/plain.md")).get_json()
    assert "target_branch" not in plain_detail["entry"]


@pytest.mark.asyncio
async def test_list_without_any_registration_keeps_item_shape(tmp_path: pathlib.Path) -> None:
    notes = tmp_path / "notes"
    _write_entry(notes, "plain.md", _PLAIN)
    client = _serve_app(notes).test_client()

    assert "target_branch" not in (await _entries(client))["plain.md"]
    assert await _entries(client, "&target_branch=develop") == {}
    assert await (await client.get("/api/branches")).get_json() == {"repositories": {}}


@pytest.mark.asyncio
async def test_list_filters_by_effective_branch(tmp_path: pathlib.Path, registered_repository: str) -> None:
    notes = tmp_path / "notes"
    _write_entry(notes, "stored.md", registered_repository, branch="maint")
    _write_entry(notes, "legacy.md", registered_repository)
    _write_entry(notes, "plain.md", _PLAIN)
    client = _serve_app(notes).test_client()

    assert sorted(await _entries(client, "&target_branch=maint")) == ["stored.md"]
    assert sorted(await _entries(client, "&target_branch=develop")) == ["legacy.md"]
    assert sorted(await _entries(client)) == ["legacy.md", "plain.md", "stored.md"]
    assert (await client.get("/api/entries?target_branch=")).status_code == 400


@pytest.mark.asyncio
async def test_branches_api_returns_registered_branches(tmp_path: pathlib.Path, registered_repository: str) -> None:
    client = _serve_app(tmp_path / "notes").test_client()

    response = await client.get("/api/branches")

    assert response.status_code == 200
    assert await response.get_json() == {
        "repositories": {registered_repository: {"branches": ["maint", "develop"], "default_branch": "develop"}}
    }


@pytest.mark.asyncio
async def test_create_saves_selected_branch_and_defaults_when_omitted(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, registered_repository: str
) -> None:
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(awi_repo, "resolve_repo_id", lambda value, **_kwargs: value)
    _patch_single_add_operations(monkeypatch)
    notes = tmp_path / "notes"
    client = _serve_app(notes).test_client()

    selected = await client.post(
        "/api/entries",
        json={"type": "awi", "messages": ["選択"], "target_repo": registered_repository, "target_branch": "maint"},
    )
    omitted = await client.post(
        "/api/entries", json={"type": "awi", "messages": ["省略"], "target_repo": registered_repository}
    )

    assert (selected.status_code, omitted.status_code) == (201, 201)
    files = _saved_files(notes)
    selected_text = files[(await selected.get_json())["filenames"][0]]
    omitted_text = files[(await omitted.get_json())["filenames"][0]]
    assert "target_branch: maint\n" in selected_text
    assert "target_branch: develop\n" in omitted_text


@pytest.mark.asyncio
async def test_create_rejects_unregistered_branch_and_branch_for_plain_repository(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, registered_repository: str
) -> None:
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(awi_repo, "resolve_repo_id", lambda value, **_kwargs: value)
    _patch_single_add_operations(monkeypatch)
    notes = tmp_path / "notes"
    client = _serve_app(notes).test_client()

    unknown = await client.post(
        "/api/entries",
        json={"type": "awi", "messages": ["x"], "target_repo": registered_repository, "target_branch": "nowhere"},
    )
    plain = await client.post(
        "/api/entries", json={"type": "awi", "messages": ["x"], "target_repo": _PLAIN, "target_branch": "maint"}
    )
    empty = await client.post(
        "/api/entries", json={"type": "awi", "messages": ["x"], "target_repo": _PLAIN, "target_branch": ""}
    )
    without_branch = await client.post("/api/entries", json={"type": "awi", "messages": ["通常"], "target_repo": _PLAIN})

    assert [unknown.status_code, plain.status_code, empty.status_code, without_branch.status_code] == [400, 400, 400, 201]
    files = _saved_files(notes)
    assert len(files) == 1
    assert "target_branch" not in next(iter(files.values()))
    assert entry_path(notes, (await without_branch.get_json())["filenames"][0]).exists()


def test_create_dialog_offers_branch_only_for_registered_repository() -> None:
    result = _run_node_ui(
        """
wiState.branchRegistry = {'github.com/example/branched': {branches: ['maint', 'develop'], default_branch: 'develop'}};
const states = [];
const observe = label => states.push({
  label, hidden: elements['create-branch-fields'].hidden, value: elements['create-branch'].value,
  options: elements['create-branch'].children.map(item => item.value)
});
elements['create-dialog'].open = true;
dialogStack.push('create-dialog');
elements['create-content'].value = '本文';
updateCreateFields();
observe('empty');
elements['create-target'].value = 'github.com/example/plain';
updateCreateBranchChoices();
observe('plain');
elements['create-target'].value = 'github.com/example/branched';
updateCreateBranchChoices();
observe('registered');
elements['create-branch'].value = 'maint';
updateCreateBranchChoices();
observe('kept');
fetchHandler = async (url) => {
  if (url.endsWith('/api/entries')) {
    return {ok: true, status: 201, statusText: 'Created', json: async () => ({filenames: ['new.md']})};
  }
  if (url.endsWith('/api/repos?status=all')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: []})};
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
};
await createEntry({preventDefault() {}});
const call = fetchCalls.find(item => item.url.endsWith('/api/entries'));
process.stdout.write(JSON.stringify({states, body: JSON.parse(call.options.body)}));
"""
    )
    by_label = {item["label"]: item for item in result["states"]}
    assert by_label["empty"]["hidden"] is True
    assert by_label["plain"]["hidden"] is True
    assert by_label["registered"] == {
        "label": "registered",
        "hidden": False,
        "value": "develop",
        "options": ["maint", "develop"],
    }
    assert by_label["kept"]["value"] == "maint"
    assert result["body"]["target_repo"] == "github.com/example/branched"
    assert result["body"]["target_branch"] == "maint"


def test_create_payload_omits_branch_for_plain_repository() -> None:
    result = _run_node_ui(
        """
wiState.branchRegistry = {'github.com/example/branched': {branches: ['maint', 'develop'], default_branch: 'develop'}};
elements['create-dialog'].open = true;
dialogStack.push('create-dialog');
elements['create-content'].value = '本文';
elements['create-target'].value = 'github.com/example/plain';
updateCreateFields();
fetchHandler = async (url) => {
  if (url.endsWith('/api/entries')) {
    return {ok: true, status: 201, statusText: 'Created', json: async () => ({filenames: ['new.md']})};
  }
  if (url.endsWith('/api/repos?status=all')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: []})};
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
};
await createEntry({preventDefault() {}});
const call = fetchCalls.find(item => item.url.endsWith('/api/entries'));
process.stdout.write(JSON.stringify({body: JSON.parse(call.options.body)}));
"""
    )
    assert result["body"] == {
        "type": "awi",
        "messages": ["本文"],
        "raw_text": "本文",
        "target_repo": "github.com/example/plain",
    }


def test_list_renders_branch_badge_and_filter_only_when_registered() -> None:
    result = _run_node_ui(
        """
const rows = entries => { wiState.entries = entries; renderEntries(); return byId('entry-list').children.map(
  row => row.children[0].children.find(cell => cell.classList.contains('status-cell')).children
    .filter(cell => cell.classList.contains('branch-badge')).map(cell => cell.textContent)); };
const entry = (filename, extra = {}) => ({
  kind: 'awi', state: 'inbox', filename, important: false, answered: null, needs_verify: false, cooldown_until: null,
  plan: false, target_repo: 'github.com/example/repo', source: null, summary: filename,
  updated_at: '2026-10-01T00:00:00+00:00', blocked: false, ...extra
});
const shown = rows([entry('a.md', {target_branch: 'maint'}), entry('b.md')]);
fetchHandler = async (url) => {
  if (url.includes('/api/branches')) {
    const repositories = {'github.com/example/repo': {branches: ['maint', 'develop'], default_branch: 'develop'}};
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repositories})};
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: []})};
};
const before = {hidden: elements['target-branch-filter'].hidden, label: elements['target-branch-filter-label'].hidden};
await loadBranchRegistry();
elements['target-branch-filter'].value = 'maint';
wiState.displayMode = 'search';
const query = buildQuery().get('target_branch');
const after = {
  hidden: elements['target-branch-filter'].hidden,
  options: elements['target-branch-filter'].children.map(item => item.value)
};
process.stdout.write(JSON.stringify({shown, before, after, query}));
"""
    )
    assert result["shown"] == [["maint"], []]
    assert result["before"] == {"hidden": True, "label": True}
    assert result["after"] == {"hidden": False, "options": ["", "maint", "develop"]}
    assert result["query"] == "maint"
