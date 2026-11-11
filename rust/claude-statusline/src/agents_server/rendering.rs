//! 状態ファイルの親子順序とstatusline行の描画。

use std::collections::HashSet;

use chrono::{DateTime, Utc};
use serde_json::Value;

use crate::render::{display_width, format_elapsed, normalize_description, render_line};
use crate::subagent::format_tokens;

use super::{DisplaySession, Session, StateFile, USAGE_LIMIT_ERROR_TYPE};

pub(crate) fn render_state_files(
    files: &[StateFile],
    columns: usize,
    now: DateTime<Utc>,
) -> Vec<String> {
    let display_sessions = flatten_sessions(files);
    let names = display_sessions
        .iter()
        .map(|item| display_name(item.session, item.depth))
        .collect::<Vec<_>>();
    let cap = columns / 2;
    let name_width = names
        .iter()
        .map(|name| display_width(name).min(cap))
        .max()
        .unwrap_or(0);
    display_sessions
        .iter()
        .zip(names)
        .map(|(item, name)| {
            // 最後に観測した行動を優先する。テキスト出力の無い区間でもツール名が進み、
            // 稼働しているかを1行で読み取れる。
            // ClaudeのAPI再試行とCodexの過負荷による自動継続の待機は、どちらも実行中の`api_error`で表す。
            let api_error = if item.session.status == "running" {
                item.session.api_error.as_ref()
            } else {
                None
            };
            // 利用上限の解除待ちは解除まで待てば同じsessionで続くため、API再試行と別の表示にする。
            let usage_limit = api_error.filter(|error| error.error_type == USAGE_LIMIT_ERROR_TYPE);
            let description = if let Some(usage_limit) = usage_limit {
                format!(
                    "対応不要: 利用上限の解除待ち {}（解除後に同じsessionを自動再開）",
                    usage_limit.limit_type.as_deref().unwrap_or("?")
                )
            } else if let Some(api_error) = api_error {
                format!(
                    "対応不要: API再試行 {}（自動再試行中）",
                    api_error.error_type
                )
            } else if matches!(
                item.session.status.as_str(),
                "completed" | "failed" | "interrupted"
            ) {
                "対応不要: 委譲元の結果回収・子孫session終了後に非表示".to_string()
            } else if item.session.last_action.is_empty() {
                item.session.progress.clone()
            } else {
                item.session.last_action.clone()
            };
            let mut right_parts = Vec::new();
            let tokens = format_tokens(
                item.session.context_tokens.as_ref(),
                item.session.context_window.as_ref(),
            );
            if let Some(usage_limit) = usage_limit {
                let now_value = Value::String(now.to_rfc3339());
                let remaining = usage_limit
                    .resets_at
                    .as_deref()
                    .and_then(|resets_at| DateTime::parse_from_rfc3339(resets_at).ok())
                    .and_then(|resets_at| {
                        format_elapsed(Some(&now_value), resets_at.with_timezone(&Utc))
                    });
                right_parts.push(remaining.map_or_else(
                    || "解除時刻確認中".to_string(),
                    |value| format!("解除まで{value}"),
                ));
                let first_at = Value::String(usage_limit.first_at.clone());
                right_parts
                    .push(format_elapsed(Some(&first_at), now).unwrap_or_else(|| "?".to_string()));
            } else if let Some(api_error) = api_error {
                let status = api_error
                    .http_status
                    .map_or_else(|| "?".to_string(), |value| value.to_string());
                right_parts.push(format!("HTTP {status}"));
                let first_at = Value::String(api_error.first_at.clone());
                right_parts
                    .push(format_elapsed(Some(&first_at), now).unwrap_or_else(|| "?".to_string()));
                right_parts.push(format!("{}回", api_error.count));
            } else {
                let started_at = Value::String(item.session.started_at.clone());
                if let Some(elapsed) = format_elapsed(Some(&started_at), now) {
                    right_parts.push(elapsed);
                }
                if let Some(tokens) = tokens.as_ref() {
                    right_parts.push(tokens.clone());
                }
                if !item.session.status.is_empty() {
                    right_parts.push(item.session.status.clone());
                }
            }
            if api_error.is_some() {
                if let Some(tokens) = tokens {
                    right_parts.push(tokens);
                }
            }
            render_line(
                &name,
                &normalize_description(&description),
                &right_parts,
                columns,
                name_width,
            )
        })
        .collect()
}

pub(super) fn flatten_sessions(files: &[StateFile]) -> Vec<DisplaySession<'_>> {
    let mut used = vec![false; files.len()];
    let mut attached = HashSet::new();
    let mut output = Vec::new();
    if let Some(root_index) = files
        .iter()
        .position(|file| file.file_name == "root.json" && file.host_session_id.is_none())
    {
        append_file_sessions(root_index, 0, files, &mut used, &mut attached, &mut output);
    }
    let identifiers = files
        .iter()
        .flat_map(|file| {
            file.sessions
                .iter()
                .map(|session| session.session_id.as_str())
        })
        .collect::<HashSet<_>>();
    for (index, file) in files.iter().enumerate() {
        if !used[index]
            && file
                .host_session_id
                .as_deref()
                .map_or(true, |host| !identifiers.contains(host))
        {
            let depth = usize::from(file.host_session_id.is_some());
            append_file_sessions(index, depth, files, &mut used, &mut attached, &mut output);
        }
    }
    for index in 0..files.len() {
        if !used[index] {
            append_file_sessions(index, 1, files, &mut used, &mut attached, &mut output);
        }
    }
    output
}

fn append_file_sessions<'a>(
    file_index: usize,
    depth: usize,
    files: &'a [StateFile],
    used: &mut [bool],
    attached: &mut HashSet<String>,
    output: &mut Vec<DisplaySession<'a>>,
) {
    if used[file_index] {
        return;
    }
    used[file_index] = true;
    let mut sessions = files[file_index].sessions.iter().collect::<Vec<_>>();
    sessions.sort_by(|left, right| left.started_at.cmp(&right.started_at));
    for session in sessions {
        if !attached.insert(session.session_id.clone()) {
            continue;
        }
        output.push(DisplaySession { session, depth });
        let child_indexes = files
            .iter()
            .enumerate()
            .filter(|(index, file)| {
                !used[*index] && file.host_session_id.as_deref() == Some(&session.session_id)
            })
            .map(|(index, _)| index)
            .collect::<Vec<_>>();
        for child_index in child_indexes {
            append_file_sessions(child_index, depth + 1, files, used, attached, output);
        }
    }
}

pub(super) fn display_name(session: &Session, depth: usize) -> String {
    let name = if session.label.is_empty() {
        &session.launch_kind
    } else {
        &session.label
    };
    let speed = if session.engine == "codex" && session.fast_mode == Some(true) {
        "@fast"
    } else {
        ""
    };
    let base = match session.model.as_deref().filter(|value| !value.is_empty()) {
        Some(model) => match session.effort.as_deref().filter(|value| !value.is_empty()) {
            Some(effort) => format!("{name} ({}:{model}/{effort}{speed})", session.engine),
            None => format!("{name} ({}:{model}{speed})", session.engine),
        },
        None => format!("{name} ({}{speed})", session.engine),
    };
    if depth == 0 {
        base
    } else {
        format!("{}└ {base}", "  ".repeat(depth - 1))
    }
}
