from agent_toolkit._testing.serve_support import _run_node_ui


def test_work_item_reference_keeps_native_destination_and_explicit_middle_click() -> None:
    result = _run_node_ui(
        """
const filename = "20260102-030405-001.md";
const link = wiLink(filename);
const opened = [];
let prevented = 0;
window.open = (...args) => opened.push(args);
for (const button of [2, 1]) {
  link.listeners.auxclick({button, preventDefault: () => prevented++});
}
process.stdout.write(JSON.stringify({text: link.textContent, href: link.href, opened, prevented}));
"""
    )
    assert result == {
        "text": "20260102-030405-001.md",
        "href": "/atk/?filename=20260102-030405-001.md",
        "opened": [["/atk/?filename=20260102-030405-001.md", "_blank", "noopener"]],
        "prevented": 1,
    }


def test_work_item_notice_distinguishes_saved_names_from_unregistered_text() -> None:
    result = _run_node_ui(
        """
const saved = "20260102-030405-001.md";
const source = "20260102-030405-002.md";
const message = `<b>改名</b>: ${source} -> ${saved}`;
showToast(message, false, [saved]);
const notice = elements['operation-notice-message'];
process.stdout.write(JSON.stringify({
  text: notice.textContent,
  destinations: notice.querySelectorAll('a').map(link => link.dataset.wiReferenceName),
  renderedAsMarkup: notice.querySelectorAll('b').length !== 0,
}));
"""
    )
    assert result == {
        "text": "<b>改名</b>: 20260102-030405-002.md -> 20260102-030405-001.md",
        "destinations": ["20260102-030405-001.md"],
        "renderedAsMarkup": False,
    }
