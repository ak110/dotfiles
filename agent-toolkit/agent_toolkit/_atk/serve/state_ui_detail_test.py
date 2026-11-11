"""serveのstateに関するui_detailの試験。"""

# pylint: disable=protected-access

import typing

from agent_toolkit._testing.serve_support import _run_node_ui


def test_assets_global_error_uses_shared_lifecycle_for_all_generators(
    host_environ: typing.Callable[[], dict[str, str]],
) -> None:
    """共通エラーの消去・再表示と、各生成元が同じ処理で表示することを検証する。"""
    result = _run_node_ui(
        """
bindEvents();
elements['refresh-button'].focus();
setGlobalError('最初のエラー');
const shown = {
  message: elements['operation-notice-message'].textContent,
  hidden: elements['operation-notice'].hidden
};
elements['operation-notice-close-button'].listeners.click();
const cleared = {
  message: elements['operation-notice-message'].textContent,
  hidden: elements['operation-notice'].hidden,
  focused
};
setGlobalError('後続のエラー');
const redisplayed = {
  message: elements['operation-notice-message'].textContent,
  hidden: elements['operation-notice'].hidden
};
const failures = [];
fetchHandler = async () => ({
  ok: false, status: 500, statusText: 'Error', json: async () => ({error: '一覧取得失敗'})
});
await loadEntries();
failures.push(elements['operation-notice-message'].textContent);
fetchHandler = async () => ({
  ok: false, status: 500, statusText: 'Error', json: async () => ({error: '対象取得失敗'})
});
await loadTargetRepos();
failures.push(elements['operation-notice-message'].textContent);
fetchHandler = async () => ({
  ok: false, status: 500, statusText: 'Error', json: async () => ({error: '詳細取得失敗'})
});
await selectEntry({state: 'inbox', filename: 'detail.md'}, new Element('detail-origin', 'BUTTON'));
failures.push(elements['operation-notice-message'].textContent);
const removed = {
  kind: 'awi', state: 'processing', filename: 'entry.md', content: '本文', body_html: '<p>本文</p>',
  frontmatter_entries: []
};
displayEntry(removed);
detailOriginKey = entryKey(removed);
elements['detail-dialog'].open = true;
fetchHandler = async url => {
  if (url !== '/atk/api/entries/entry.md') throw new Error('想定外のURL: ' + url);
  return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
};
await reloadOpenDetailFromExternalChange();
failures.push(elements['operation-notice-message'].textContent);
refreshKnownEntries = async () => { throw new Error('SSE更新失敗'); };
fetchHandler = async () => ({
  ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: [], repos: []})
});
await reloadFromExternalChange();
await Promise.resolve();
failures.push(elements['operation-notice-message'].textContent);
deliverOperationMessage('ダイアログ外失敗', true);
failures.push(elements['operation-notice-message'].textContent);
refreshKnownEntries = async () => { throw new Error('初期化失敗'); };
initializeApp();
await initialization;
failures.push(elements['operation-notice-message'].textContent);
process.stdout.write(JSON.stringify({shown, cleared, redisplayed, failures}));
""",
        environment=host_environ(),
    )
    assert result == {
        "shown": {"message": "［WI / node-test / /notes / 操作］最初のエラー", "hidden": False},
        "cleared": {
            "message": "［WI / node-test / /notes / 操作］最初のエラー",
            "hidden": True,
            "focused": "refresh-button",
        },
        "redisplayed": {"message": "［WI / node-test / /notes / 操作］後続のエラー", "hidden": False},
        "failures": [
            "［WI / node-test / /notes / 一覧取得］一覧取得失敗",
            "［WI / node-test / /notes / 対象リポジトリ一覧取得］対象取得失敗",
            "［WI / node-test / /notes / detail.md］詳細取得失敗",
            "entry.mdは削除されたため表示できません。一覧を更新しました。",
            "［WI / node-test / /notes / 操作］SSE更新失敗",
            "［WI / node-test / /notes / 操作］ダイアログ外失敗",
            "［WI / node-test / /notes / 操作］初期化失敗",
        ],
    }


def test_assets_restore_focus_on_escape_and_focus_delete_close_first() -> None:
    """Escapeでフォーカスを操作の起点へ戻し、破壊的な操作の確認では取り消しの「閉じる」へ初期フォーカスを置く。"""
    result = _run_node_ui(
        """
const origin = new Element('create-origin', 'BUTTON');
attachDialogCloseHandlers('create-dialog', 'create-close-button', () => closeDialog(elements['create-dialog']));
openDialog(elements['create-dialog'], origin, elements['create-content']);
elements['create-dialog'].listeners.cancel({preventDefault() {}});
const restored = focused;
displayEntry({
  kind: 'awi', state: 'processing', filename: 'entry.md', answered: null,
  summary: '要約', target_repo: 'example/repo', content: 'raw', body_html: '<p>本文</p>',
  question_type: 'free-form', choices: [], frontmatter_entries: []
});
elements['detail-dialog'].open = true;
dialogStack.push('detail-dialog');
openDeleteDialog();
process.stdout.write(JSON.stringify({
  restored,
  deleteInitial: focused,
  closeSizeClass: elements['delete-close-button'].className,
  detailStillOpen: elements['detail-dialog'].open
}));
"""
    )
    assert result == {
        "restored": "create-origin",
        "deleteInitial": "delete-close-button",
        "closeSizeClass": "dialog-close",
        "detailStillOpen": True,
    }


def test_assets_reload_open_detail_from_sse_and_preserve_editing_input() -> None:
    """SSE更新時に閲覧本文を再取得し、編集中は入力を保持して消失時に閉じる。"""
    result = _run_node_ui(
        """
const listed = {
  kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
  summary: '一覧要約', target_repo: 'example/repo', frontmatter_entries: []
};
let detailContent = '外部更新後の本文';
let deleted = false;
fetchHandler = async url => {
  if (url.includes('/api/repos?')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: ['example/repo']})};
  }
  if (url.includes('/api/entries?')) {
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({entries: deleted ? [] : [listed], warnings: []})
    };
  }
  if (url.includes('/api/entries/')) {
    if (deleted) {
      return {ok: false, status: 404, statusText: 'Not Found', json: async () => ({error: 'not found'})};
    }
    return {
      ok: true, status: 200, statusText: 'OK',
      json: async () => ({entry: {...listed, content: detailContent, body_html: `<p>${detailContent}</p>`}})
    };
  }
  throw new Error('想定外のURL: ' + url);
};
const original = {...listed, content: '更新前の本文', body_html: '<p>更新前の本文</p>'};
entries = [original];
renderEntries();
const origin = elements['entry-list'].children[0].children[0];
displayEntry(original);
detailOriginKey = entryKey(original);
openDialog(elements['detail-dialog'], origin, elements['detail-dialog-body']);
await reloadFromExternalChange();
const viewed = elements['detail-content'].innerHTML;
enterEdit();
elements['edit-content'].value = 'ユーザーの未保存本文';
detailContent = '編集中の外部更新';
await reloadFromExternalChange();
const editing = {
  input: elements['edit-content'].value,
  baseline: currentEntry.content,
  alert: elements['detail-alert'].textContent,
  saveDisabled: elements['save-entry-button'].disabled,
  open: elements['detail-dialog'].open
};
// 回答欄は回答できるUWIの閲覧中に常設されるため、UWIを開き直した状態で入力途中の回答を外部更新から守る。
listed.kind = 'uwi';
detailRefreshRequired = false;
setTextMessage('detail-alert', '');
currentEntry = {...currentEntry, kind: 'uwi', answer: null, content: detailContent};
setDetailMode('view');
elements['answer-input'].value = 'ユーザーの未保存回答';
detailContent = '回答中の外部更新';
await reloadFromExternalChange();
const answering = {
  input: elements['answer-input'].value,
  alert: elements['detail-alert'].textContent,
  saveDisabled: elements['save-answer-button'].disabled,
  open: elements['detail-dialog'].open
};
deleted = true;
await reloadFromExternalChange();
process.stdout.write(JSON.stringify({
  viewed,
  detailRequests: fetchCalls.filter(call => call.url.includes('/api/entries/')).length,
  editing,
  answering,
  afterDelete: {open: elements['detail-dialog'].open, focused}
}));
"""
    )
    assert result["viewed"] == "<p>外部更新後の本文</p>"
    assert result["detailRequests"] >= 3
    assert result["editing"] == {
        "input": "ユーザーの未保存本文",
        "baseline": "外部更新後の本文",
        "alert": "外部で項目が更新されました。入力を保持しています。詳細を閉じて開き直してから保存してください。",
        "saveDisabled": True,
        "open": True,
    }
    assert result["answering"] == {
        "input": "ユーザーの未保存回答",
        "alert": "外部で項目が更新されました。入力を保持しています。詳細を閉じて開き直してから保存してください。",
        "saveDisabled": True,
        "open": True,
    }
    assert result["afterDelete"] == {"open": False, "focused": "empty-all-states-button"}


def test_assets_delete_conflict_requires_detail_reload() -> None:
    """削除競合後は古い詳細から再試行させず、開き直しを要求する。"""
    result = _run_node_ui(
        """
const entry = {
  kind: 'awi', state: 'inbox', filename: 'entry.md', answered: null,
  summary: '削除対象', target_repo: 'example/repo', content: '取得時本文', body_html: '<p>取得時本文</p>',
  frontmatter_entries: []
};
displayEntry(entry);
openDialog(elements['detail-dialog'], new Element('origin', 'BUTTON'), elements['detail-dialog-body']);
openDeleteDialog();
fetchHandler = async () => ({
  ok: false, status: 409, statusText: 'Conflict',
  json: async () => ({error: '編集中に他プロセスが対象を変更しました', code: 'edit_conflict'})
});
await deleteEntry({preventDefault() {}});
process.stdout.write(JSON.stringify({
  deleteOpen: elements['delete-dialog'].open,
  detailOpen: elements['detail-dialog'].open,
  deleteDisabled: elements['delete-button'].disabled,
  editDisabled: elements['edit-button'].disabled,
  alert: elements['detail-alert'].textContent
}));
"""
    )
    assert result["deleteOpen"] is False
    assert result["detailOpen"] is True
    assert result["deleteDisabled"] is True
    assert result["editDisabled"] is True
    assert "詳細を閉じて開き直してから削除してください" in result["alert"]


def test_operation_result_uses_active_dialog_or_page_notification() -> None:
    """操作結果は最上位ダイアログへ送り、ダイアログが無い場合だけページ通知へ送る。"""
    result = _run_node_ui(
        """
const dialogResults = {};
for (const name of ['detail', 'create', 'delete']) {
  elements['operation-notice'].hidden = true;
  elements[`${name}-dialog`].open = true;
  dialogStack.push(`${name}-dialog`);
  deliverOperationMessage(`${name}失敗`, true);
  dialogResults[name] = {
    alert: elements[`${name}-alert`].textContent,
    status: elements[`${name}-status`].textContent,
    pageHidden: elements['operation-notice'].hidden
  };
  closeDialog(elements[`${name}-dialog`]);
}
deliverOperationMessage('保存完了', true);
process.stdout.write(JSON.stringify({
  dialogResults,
  message: elements['operation-notice-message'].textContent,
  error: elements['operation-notice'].dataset.error,
  role: elements['operation-notice'].attributes.role,
  closeLabel: elements['operation-notice-close-button'].attributes['aria-label']
}));
"""
    )
    assert result == {
        "dialogResults": {
            "detail": {"alert": "detail失敗", "status": "", "pageHidden": True},
            "create": {"alert": "create失敗", "status": "", "pageHidden": True},
            "delete": {"alert": "delete失敗", "status": "", "pageHidden": True},
        },
        "message": "［WI / node-test / /notes / 操作］保存完了",
        "error": "true",
        "role": "alert",
        "closeLabel": "操作通知を閉じる",
    }


def test_field_errors_mark_and_focus_first_invalid_control() -> None:
    """入力エラーを関連付け、最初の不正入力のフォーカスと対応エラーの表示を選ぶ。"""
    result = _run_node_ui(
        """
const scrolled = [];
elements['create-content'].setAttribute('aria-describedby', 'create-content-error');
elements['create-content-error'].scrollIntoView = () => scrolled.push('create-content-error');
elements['create-target'].setAttribute('aria-describedby', 'create-target-error');
elements['create-target-error'].scrollIntoView = () => scrolled.push('create-target-error');
setFieldError(elements['create-content'], elements['create-content-error'], '本文が必要です');
setFieldError(elements['create-target'], elements['create-target-error'], '対象が必要です');
const first = firstInvalid([elements['create-content'], elements['create-target']]);
process.stdout.write(JSON.stringify({
  first: first.id,
  focused,
  scrolled,
  contentInvalid: elements['create-content'].attributes['aria-invalid'],
  targetInvalid: elements['create-target'].attributes['aria-invalid'],
  contentError: elements['create-content-error'].textContent
}));
"""
    )
    assert result == {
        "first": "create-content",
        "focused": "create-content",
        "scrolled": ["create-content-error"],
        "contentInvalid": "true",
        "targetInvalid": "true",
        "contentError": "本文が必要です",
    }
