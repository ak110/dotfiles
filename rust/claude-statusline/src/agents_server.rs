//! agents_serverが出力した状態ファイルをClaude Codeのstatusline行へ変換する。
//!
//! 状態ディレクトリはPython側の`platformdirs.user_state_dir("agent-toolkit")`と合わせる。
//! Linuxは絶対パスの`XDG_STATE_HOME`を優先し、無ければ`HOME/.local/state`、
//! Windowsは`LOCALAPPDATA`の配下に`agent-toolkit`を結合する。

use std::fs;
use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};
use serde_json::{Map, Value};

use crate::subagent::{
    display_width, format_elapsed, normalize_description, render_line, short_model_name,
    DEFAULT_COLUMNS, NAME_WIDTH_DIVISOR,
};

const STATE_VERSION: u64 = 1;

#[derive(Debug)]
pub(crate) struct StateFile {
    file_name: String,
    host_session_id: Option<String>,
    sessions: Vec<Session>,
}

#[derive(Debug)]
struct Session {
    session_id: String,
    engine: String,
    model: Option<String>,
    model_type: String,
    launch_kind: String,
    status: String,
    progress: String,
    label: String,
    started_at: String,
}

#[derive(Debug)]
struct DisplaySession<'a> {
    session: &'a Session,
    depth: usize,
}

/// OS種別と環境変数からルートsessionの状態ディレクトリを解決する。
pub(crate) fn state_directory(
    os: &str,
    env: impl Fn(&str) -> Option<String>,
    root_session_id: &str,
) -> Option<PathBuf> {
    if !valid_session_id(root_session_id) {
        return None;
    }
    let state_base = match os {
        "windows" => PathBuf::from(env("LOCALAPPDATA")?),
        _ => env("XDG_STATE_HOME")
            .map(PathBuf::from)
            .filter(|path| path.is_absolute())
            .or_else(|| env("HOME").map(|home| PathBuf::from(home).join(".local").join("state")))?,
    };
    Some(
        state_base
            .join("agent-toolkit")
            .join("agents-server")
            .join(root_session_id),
    )
}

fn valid_session_id(session_id: &str) -> bool {
    !session_id.is_empty()
        && session_id
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-'))
}

/// 指定ディレクトリ内の状態ファイルを全て読み、解釈できるものだけを返す。
pub(crate) fn read_state_files(directory: &Path) -> Vec<StateFile> {
    let Ok(entries) = fs::read_dir(directory) else {
        return Vec::new();
    };
    let mut paths = entries
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.is_file() && is_state_file(path))
        .collect::<Vec<_>>();
    paths.sort();
    paths
        .into_iter()
        .filter_map(|path| {
            let file_name = path.file_name()?.to_str()?.to_string();
            let raw = fs::read_to_string(path).ok()?;
            let value = serde_json::from_str::<Value>(&raw).ok()?;
            parse_state_file(file_name, &value)
        })
        .collect()
}

fn is_state_file(path: &Path) -> bool {
    path.extension().and_then(|extension| extension.to_str()) == Some("json")
}

fn parse_state_file(file_name: String, value: &Value) -> Option<StateFile> {
    let object = value.as_object()?;
    if object.get("version")?.as_u64()? != STATE_VERSION {
        return None;
    }
    let host_session_id = optional_string(object, "host_session_id")?;
    object.get("updated_at")?.as_str()?;
    let sessions = object
        .get("sessions")?
        .as_array()?
        .iter()
        .filter_map(parse_session)
        .collect();
    Some(StateFile {
        file_name,
        host_session_id,
        sessions,
    })
}

fn optional_string(object: &Map<String, Value>, key: &str) -> Option<Option<String>> {
    match object.get(key)? {
        Value::Null => Some(None),
        Value::String(value) => Some(Some(value.clone())),
        _ => None,
    }
}

fn parse_session(value: &Value) -> Option<Session> {
    let object = value.as_object()?;
    let model = optional_string(object, "model")?;
    optional_string(object, "effort")?;
    let session = Session {
        session_id: required_string(object, "session_id")?,
        engine: required_string(object, "engine")?,
        model,
        model_type: required_string(object, "model_type")?,
        launch_kind: required_string(object, "launch_kind")?,
        status: required_string(object, "status")?,
        progress: required_string(object, "progress")?,
        label: required_string(object, "label")?,
        started_at: required_string(object, "started_at")?,
    };
    DateTime::parse_from_rfc3339(&session.started_at).ok()?;
    Some(session)
}

fn required_string(object: &Map<String, Value>, key: &str) -> Option<String> {
    object.get(key)?.as_str().map(ToString::to_string)
}

/// 解釈済み状態と表示条件から、sessionごとの行を返す。
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
    let cap = columns / NAME_WIDTH_DIVISOR;
    let name_width = names
        .iter()
        .map(|name| display_width(name).min(cap))
        .max()
        .unwrap_or(0);

    display_sessions
        .iter()
        .zip(names)
        .map(|(item, name)| {
            let description = if item.session.progress.is_empty() {
                &item.session.label
            } else {
                &item.session.progress
            };
            let mut right_parts = Vec::new();
            let started_at = Value::String(item.session.started_at.clone());
            if let Some(elapsed) = format_elapsed(Some(&started_at), now) {
                right_parts.push(elapsed);
            }
            if !item.session.status.is_empty() {
                right_parts.push(item.session.status.clone());
            }
            render_line(
                &name,
                &normalize_description(description),
                &right_parts,
                columns,
                name_width,
            )
        })
        .collect()
}

fn flatten_sessions(files: &[StateFile]) -> Vec<DisplaySession<'_>> {
    let mut used = vec![false; files.len()];
    let mut output = Vec::new();
    if let Some(root_index) = files
        .iter()
        .position(|file| file.file_name == "root.json" && file.host_session_id.is_none())
    {
        append_file_sessions(root_index, 0, files, &mut used, &mut output);
    }
    for index in 0..files.len() {
        if !used[index] {
            append_file_sessions(index, 1, files, &mut used, &mut output);
        }
    }
    output
}

fn append_file_sessions<'a>(
    file_index: usize,
    depth: usize,
    files: &'a [StateFile],
    used: &mut [bool],
    output: &mut Vec<DisplaySession<'a>>,
) {
    if used[file_index] {
        return;
    }
    used[file_index] = true;
    let mut sessions = files[file_index].sessions.iter().collect::<Vec<_>>();
    sessions.sort_by(|left, right| left.started_at.cmp(&right.started_at));
    for session in sessions {
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
            append_file_sessions(child_index, depth + 1, files, used, output);
        }
    }
}

fn display_name(session: &Session, depth: usize) -> String {
    let name = if session.launch_kind == "delegate" {
        &session.model_type
    } else {
        &session.launch_kind
    };
    let engine = match session.engine.as_str() {
        "claude" => "Claude",
        "codex" => "Codex",
        other => other,
    };
    let model = session
        .model
        .as_deref()
        .filter(|value| !value.is_empty())
        .map(short_model_name);
    let base = match model {
        Some(model) => format!("{name} ({engine}/{model})"),
        None => format!("{name} ({engine})"),
    };
    if depth == 0 {
        base
    } else {
        format!("{}└ {base}", "  ".repeat(depth - 1))
    }
}

fn terminal_columns() -> usize {
    std::env::var("COLUMNS")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_COLUMNS)
}

/// 現在の環境から状態ファイルを読み、statuslineに追加する行を返す。
pub(crate) fn render_for_session(root_session_id: &str) -> Vec<String> {
    let Some(directory) = state_directory(
        std::env::consts::OS,
        |key| std::env::var(key).ok(),
        root_session_id,
    ) else {
        return Vec::new();
    };
    let files = read_state_files(&directory);
    render_state_files(&files, terminal_columns(), Utc::now())
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    use super::*;

    fn env(values: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let values = values
            .iter()
            .map(|(key, value)| ((*key).to_string(), (*value).to_string()))
            .collect::<HashMap<_, _>>();
        move |key| values.get(key).cloned()
    }

    fn state_file(file_name: &str, host: Value, sessions: Value) -> StateFile {
        parse_state_file(
            file_name.to_string(),
            &serde_json::json!({
                "version": 1,
                "host_session_id": host,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "sessions": sessions,
            }),
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

    #[test]
    fn state_directory_follows_platformdirs_rules() {
        assert_eq!(
            state_directory(
                "linux",
                env(&[("XDG_STATE_HOME", "/state"), ("HOME", "/home/test")]),
                "root-1"
            ),
            Some(PathBuf::from("/state/agent-toolkit/agents-server/root-1"))
        );
        assert_eq!(
            state_directory(
                "linux",
                env(&[("XDG_STATE_HOME", "relative"), ("HOME", "/home/test")]),
                "root-1"
            ),
            Some(PathBuf::from(
                "/home/test/.local/state/agent-toolkit/agents-server/root-1"
            ))
        );
        assert_eq!(state_directory("linux", env(&[]), "root-1"), None);
        assert_eq!(
            state_directory(
                "windows",
                env(&[("LOCALAPPDATA", "C:\\Users\\test\\AppData\\Local")]),
                "root-1"
            ),
            Some(
                PathBuf::from("C:\\Users\\test\\AppData\\Local")
                    .join("agent-toolkit")
                    .join("agents-server")
                    .join("root-1")
            )
        );
        assert_eq!(state_directory("windows", env(&[]), "root-1"), None);
        assert_eq!(
            state_directory("linux", env(&[("HOME", "/home/test")]), "bad/id"),
            None
        );
    }

    #[test]
    fn rendering_orders_nested_and_orphan_sessions() {
        let root = state_file(
            "root.json",
            Value::Null,
            serde_json::json!([
                session(
                    "root-later",
                    "claude",
                    Value::Null,
                    ("unused", "shell"),
                    ("", "shell label"),
                    "2025-12-31T23:59:30+00:00"
                ),
                session(
                    "root-first",
                    "claude",
                    Value::String("claude-opus-4-8".to_string()),
                    ("impl", "delegate"),
                    ("", "implementation label"),
                    "2025-12-31T23:59:15+00:00"
                )
            ]),
        );
        let nested = state_file(
            "root-first.json",
            Value::String("root-first".to_string()),
            serde_json::json!([session(
                "nested",
                "codex",
                Value::String("gpt-5.6-terra".to_string()),
                ("explore_fast", "explore"),
                ("latest progress", "fallback label"),
                "2025-12-31T23:59:40+00:00"
            )]),
        );
        let grandchild = state_file(
            "nested.json",
            Value::String("nested".to_string()),
            serde_json::json!([session(
                "grandchild",
                "claude",
                Value::String("claude-sonnet-4-6".to_string()),
                ("review", "delegate"),
                ("", "grandchild label"),
                "2025-12-31T23:59:45+00:00"
            )]),
        );
        let orphan = state_file(
            "orphan.json",
            Value::String("missing-parent".to_string()),
            serde_json::json!([session(
                "orphan",
                "claude",
                Value::Null,
                ("unused", "shell"),
                ("", "orphan label"),
                "2025-12-31T23:59:50+00:00"
            )]),
        );
        let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
            .unwrap()
            .with_timezone(&Utc);
        let lines = render_state_files(&[grandchild, nested, orphan, root], 100, now);

        assert_eq!(lines.len(), 5);
        assert!(lines[0].starts_with("impl (Claude/Opus)"));
        assert!(lines[0].contains("implementation label"));
        assert!(lines[0].ends_with("45s · running"));
        assert!(lines[1].starts_with("└ explore (Codex/gpt-5.6-terra)"));
        assert!(lines[1].contains("latest progress"));
        assert!(lines[2].starts_with("  └ review (Claude/Sonnet)"));
        assert!(lines[2].contains("grandchild label"));
        assert!(lines[3].starts_with("shell (Claude)"));
        assert!(lines[4].starts_with("└ shell (Claude)"));
        assert!(lines.iter().all(|line| display_width(line) <= 100));
    }

    #[test]
    fn invalid_versions_and_incomplete_sessions_are_ignored() {
        let wrong_version = serde_json::json!({
            "version": 2,
            "host_session_id": null,
            "updated_at": "2026-01-01T00:00:00+00:00",
            "sessions": [],
        });
        assert!(parse_state_file("root.json".to_string(), &wrong_version).is_none());

        let file = state_file(
            "root.json",
            Value::Null,
            serde_json::json!([
                session(
                    "valid",
                    "claude",
                    Value::Null,
                    ("unused", "shell"),
                    ("", "valid"),
                    "2025-12-31T23:59:30+00:00"
                ),
                {"session_id": "missing-fields"}
            ]),
        );
        let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
            .unwrap()
            .with_timezone(&Utc);
        assert_eq!(render_state_files(&[file], 80, now).len(), 1);
    }

    #[test]
    fn atomic_write_temporary_files_are_not_state_files() {
        assert!(is_state_file(Path::new("root.json")));
        assert!(is_state_file(Path::new("host-session.json")));
        assert!(!is_state_file(Path::new(".root.json.random.tmp")));
    }
}
