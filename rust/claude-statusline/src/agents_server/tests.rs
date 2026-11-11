use std::fs;
use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};
use serde_json::Value;

use crate::render::{display_width, DEFAULT_COLUMNS};

use super::rendering::{display_name, flatten_sessions};
use super::state::{is_state_file, parse_session, parse_state_file, retain_displayed_sessions};
use super::*;

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

fn env(values: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
    let values = values
        .iter()
        .map(|(key, value)| ((*key).to_string(), (*value).to_string()))
        .collect::<HashMap<_, _>>();
    move |key| values.get(key).cloned()
}

fn state_file(file_name: &str, host: Value, sessions: Value) -> StateFile {
    let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
        .unwrap()
        .with_timezone(&Utc);
    parse_state_file(
        file_name.to_string(),
        &serde_json::json!({
            "version": 1,
            "host_session_id": host,
            "updated_at": "2026-01-01T00:00:00+00:00",
            "sessions": sessions,
        }),
        now,
    )
    .unwrap()
}

fn session(
    session_id: &str,
    engine: &str,
    model: Value,
    launch: (&str, &str),
    description: (&str, &str),
    started_at: &str,
) -> Value {
    let (model_type, launch_kind) = launch;
    let (progress, label) = description;
    serde_json::json!({
        "session_id": session_id,
        "engine": engine,
        "model": model,
        "effort": "high",
        "model_type": model_type,
        "launch_kind": launch_kind,
        "status": "running",
        "progress": progress,
        "label": label,
        "started_at": started_at,
    })
}

/// agent-toolkitの`status_file.py`と共有する状態ファイルの契約。
///
/// 同じfixtureをリポジトリ直下の`claude_statusline_invariant_test.py`が書き込み側と比べる。
/// 版・期限・キー・状態ディレクトリの構成の一方だけを変えると、どちらかのテストが失敗する。
const STATE_CONTRACT: &str = include_str!("../../testdata/agents_server_state.json");

fn sorted_keys(value: &Value) -> Vec<String> {
    let mut keys = value
        .as_object()
        .unwrap()
        .keys()
        .cloned()
        .collect::<Vec<_>>();
    keys.sort();
    keys
}

fn string_list(value: &Value) -> Vec<String> {
    let mut items = value
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item.as_str().unwrap().to_string())
        .collect::<Vec<_>>();
    items.sort();
    items
}

fn status_session(session_id: &str, status: &str, started_at: &str) -> Value {
    let mut value = session(
        session_id,
        "claude",
        Value::Null,
        ("impl", "delegate"),
        ("", session_id),
        started_at,
    );
    value["status"] = Value::String(status.to_string());
    value
}

fn displayed_ids(files: &[StateFile]) -> Vec<String> {
    flatten_sessions(files)
        .iter()
        .map(|item| item.session.session_id.clone())
        .collect()
}

mod lifecycle;
mod rendering;
mod state;
