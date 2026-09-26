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
    display_width, format_elapsed, normalize_description, render_line, short_model_name, truncate,
    DEFAULT_COLUMNS, NAME_WIDTH_DIVISOR,
};

const STATE_VERSION: u64 = 1;
const HEARTBEAT_EXPIRY_SECONDS: i64 = 120;
// Claude Codeの描画は先頭の字下げ2セルと行末の2セルを確保する。
// 確保幅は描画された行の表示幅とCOLUMNSの差から導出した。
const STATUSLINE_RESERVED_COLUMNS: usize = 4;
// 識別名の列は、右端へ並ぶ経過時間とstatusの位置を行ごとにそろえるため上限幅を持つ。
const LABEL_WIDTH_CAP: usize = 16;

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
    last_action: String,
    label: String,
    started_at: String,
    api_error: Option<ApiError>,
}

#[derive(Debug)]
struct ApiError {
    error_type: String,
    http_status: Option<u64>,
    first_at: String,
    count: u64,
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
    let agents_server_directory = state_base.join("agent-toolkit").join("agents-server");
    let alias_path = agents_server_directory
        .join("aliases")
        .join(format!("{root_session_id}.json"));
    let resolved_root = fs::read_to_string(alias_path)
        .ok()
        .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
        .and_then(|value| {
            if value.get("version")?.as_u64()? != STATE_VERSION {
                return None;
            }
            value.get("root_session_id")?.as_str().map(str::to_string)
        })
        .filter(|value| valid_session_id(value))
        .filter(|value| agents_server_directory.join(value).is_dir())
        .unwrap_or_else(|| root_session_id.to_string());
    Some(agents_server_directory.join(resolved_root))
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
            let mut state_file = parse_state_file(file_name, &value, Utc::now())?;
            retain_sessions_with_results(&mut state_file, |session_id| {
                directory
                    .join("results")
                    .join(format!("{session_id}.json"))
                    .is_file()
            });
            Some(state_file)
        })
        .collect()
}

fn retain_sessions_with_results(
    state_file: &mut StateFile,
    mut result_exists: impl FnMut(&str) -> bool,
) {
    state_file.sessions.retain(|session| {
        !matches!(
            session.status.as_str(),
            "completed" | "failed" | "interrupted"
        ) || result_exists(&session.session_id)
    });
}

fn is_state_file(path: &Path) -> bool {
    path.extension().and_then(|extension| extension.to_str()) == Some("json")
}

fn parse_state_file(file_name: String, value: &Value, now: DateTime<Utc>) -> Option<StateFile> {
    let object = value.as_object()?;
    if object.get("version")?.as_u64()? != STATE_VERSION {
        return None;
    }
    let host_session_id = optional_string(object, "host_session_id")?;
    if let Some(heartbeat_at) = object.get("heartbeat_at") {
        let heartbeat = DateTime::parse_from_rfc3339(heartbeat_at.as_str()?)
            .ok()?
            .with_timezone(&Utc);
        if now.signed_duration_since(heartbeat).num_seconds() > HEARTBEAT_EXPIRY_SECONDS {
            return None;
        }
    }
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

/// 不在とnullを空文字として受理し、文字列以外だけを解釈失敗として返す。
fn absent_as_empty_string(object: &Map<String, Value>, key: &str) -> Option<String> {
    match object.get(key) {
        None | Some(Value::Null) => Some(String::new()),
        Some(Value::String(value)) => Some(value.clone()),
        Some(_) => None,
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
        // `last_action`は任意項目とする。長命なMCPサーバープロセスは起動時に読み込んだ
        // モジュールを保持し続けるため、statuslineだけが先に更新される区間では
        // 当該項目を持たない状態ファイルが書かれ続ける。必須にすると当該区間で行が消える。
        last_action: absent_as_empty_string(object, "last_action")?,
        label: required_string(object, "label")?,
        started_at: required_string(object, "started_at")?,
        api_error: object.get("api_error").and_then(parse_api_error),
    };
    DateTime::parse_from_rfc3339(&session.started_at).ok()?;
    Some(session)
}

fn parse_api_error(value: &Value) -> Option<ApiError> {
    let object = value.as_object()?;
    let error_type = object.get("type")?.as_str()?;
    if error_type.is_empty() {
        return None;
    }
    let http_status = match object.get("http_status")? {
        Value::Null => None,
        Value::Number(number) => {
            let status = number.as_u64()?;
            if !(100..=599).contains(&status) {
                return None;
            }
            Some(status)
        }
        _ => return None,
    };
    let first_at = object.get("first_at")?.as_str()?;
    DateTime::parse_from_rfc3339(first_at).ok()?;
    let count = object.get("count")?.as_u64()?;
    if count == 0 {
        return None;
    }
    Some(ApiError {
        error_type: error_type.to_string(),
        http_status,
        first_at: first_at.to_string(),
        count,
    })
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
    let label_width = display_sessions
        .iter()
        .map(|item| display_width(&item.session.label))
        .max()
        .unwrap_or(0)
        .min(LABEL_WIDTH_CAP);

    display_sessions
        .iter()
        .zip(names)
        .map(|(item, name)| {
            // 最後に観測した行動を優先する。テキスト出力の無い区間でもツール名が進み、
            // 稼働しているかを1行で読み取れる。
            let api_error = if item.session.engine == "claude" && item.session.status == "running" {
                item.session.api_error.as_ref()
            } else {
                None
            };
            let description = if let Some(api_error) = api_error {
                format!("API再試行 {}", api_error.error_type)
            } else if item.session.last_action.is_empty() {
                item.session.progress.clone()
            } else {
                item.session.last_action.clone()
            };
            let mut right_parts = Vec::new();
            if api_error.is_none() && label_width > 0 {
                let fitted = truncate(&item.session.label, label_width);
                let pad = label_width.saturating_sub(display_width(&fitted));
                right_parts.push(format!("{fitted}{}", " ".repeat(pad)));
            }
            if let Some(api_error) = api_error {
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
                if !item.session.status.is_empty() {
                    right_parts.push(item.session.status.clone());
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

fn statusline_columns(columns: usize) -> usize {
    columns.saturating_sub(STATUSLINE_RESERVED_COLUMNS)
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
    render_state_files(&files, statusline_columns(terminal_columns()), Utc::now())
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;
    use std::time::{SystemTime, UNIX_EPOCH};

    use super::*;

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
    fn state_directory_resolves_only_alias_with_existing_target() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let state_root = std::env::temp_dir().join(format!(
            "claude-statusline-alias-{}-{nonce}",
            std::process::id()
        ));
        let agents_server = state_root.join("agent-toolkit").join("agents-server");
        fs::create_dir_all(agents_server.join("aliases")).unwrap();
        fs::write(
            agents_server.join("aliases").join("current-session.json"),
            r#"{"version":1,"root_session_id":"root-session"}"#,
        )
        .unwrap();
        let environment = env(&[("XDG_STATE_HOME", state_root.to_str().unwrap())]);

        assert_eq!(
            state_directory("linux", &environment, "current-session"),
            Some(agents_server.join("current-session"))
        );
        fs::create_dir(agents_server.join("root-session")).unwrap();
        assert_eq!(
            state_directory("linux", &environment, "current-session"),
            Some(agents_server.join("root-session"))
        );

        fs::remove_dir_all(&state_root).unwrap();
    }

    #[test]
    fn statusline_width_reserves_claude_render_margin() {
        assert_eq!(statusline_columns(80), 76);
        assert_eq!(statusline_columns(4), 0);
        assert_eq!(statusline_columns(3), 0);
        assert_eq!(statusline_columns(1), 0);
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
        // 識別名の列は上限16セルで切り詰める。
        assert!(lines[0].contains("implementation …"));
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
    fn rendering_prefers_last_action_over_progress() {
        let mut with_action = session(
            "with-action",
            "claude",
            Value::Null,
            ("impl", "delegate"),
            ("latest progress", "fallback label"),
            "2025-12-31T23:59:30+00:00",
        );
        with_action["last_action"] = Value::String("Bash".to_string());
        let mut empty_action = session(
            "empty-action",
            "claude",
            Value::Null,
            ("impl", "delegate"),
            ("latest progress", "fallback label"),
            "2025-12-31T23:59:31+00:00",
        );
        empty_action["last_action"] = Value::String(String::new());
        let without_action = session(
            "without-action",
            "claude",
            Value::Null,
            ("impl", "delegate"),
            ("", "fallback label"),
            "2025-12-31T23:59:32+00:00",
        );
        let root = state_file(
            "root.json",
            Value::Null,
            serde_json::json!([with_action, empty_action, without_action]),
        );
        let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
            .unwrap()
            .with_timezone(&Utc);

        let lines = render_state_files(&[root], 200, now);

        assert_eq!(lines.len(), 3, "{lines:?}");
        assert!(lines[0].contains("Bash"), "{lines:?}");
        assert!(!lines[0].contains("latest progress"), "{lines:?}");
        assert!(lines[1].contains("latest progress"), "{lines:?}");
        assert!(!lines[2].contains("Bash"), "{lines:?}");
        assert!(!lines[2].contains("latest progress"), "{lines:?}");
    }

    #[test]
    fn rendering_api_error_from_state_file_shows_retry_and_recovers() {
        let mut retrying = session(
            "retrying",
            "claude",
            Value::Null,
            ("impl", "delegate"),
            ("latest progress", "lane-01"),
            "2025-12-31T23:59:00+00:00",
        );
        retrying["last_action"] = Value::String("Bash".to_string());
        retrying["api_error"] = serde_json::json!({
            "type": "rate_limit_error",
            "http_status": 429,
            "first_at": "2025-12-31T23:59:20+00:00",
            "count": 3,
        });
        let mut recovered = retrying.clone();
        recovered.as_object_mut().unwrap().remove("api_error");
        let mut other_engine = retrying.clone();
        other_engine["engine"] = Value::String("codex".to_string());
        let mut terminal = retrying.clone();
        terminal["status"] = Value::String("completed".to_string());
        let mut malformed = retrying.clone();
        malformed["api_error"]["count"] = Value::String("3".to_string());
        let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
            .unwrap()
            .with_timezone(&Utc);

        let files = [
            state_file("retrying.json", Value::Null, serde_json::json!([retrying])),
            state_file(
                "recovered.json",
                Value::Null,
                serde_json::json!([recovered]),
            ),
            state_file("other.json", Value::Null, serde_json::json!([other_engine])),
            state_file("terminal.json", Value::Null, serde_json::json!([terminal])),
            state_file(
                "malformed.json",
                Value::Null,
                serde_json::json!([malformed]),
            ),
        ];
        let lines = render_state_files(&files, 80, now);

        assert_eq!(lines.len(), 5);
        assert!(lines[0].contains("API再試行 rate_limit_error"));
        assert!(lines[0].contains("HTTP 429"));
        assert!(lines[0].contains("40s"));
        assert!(lines[0].contains("3回"));
        assert!(lines.iter().all(|line| display_width(line) <= 80));
        assert!(!lines[0].contains("Bash"));
        assert!(lines[1..].iter().all(|line| !line.contains("API再試行")));
        assert!(lines[1..].iter().all(|line| line.contains("Bash")));
    }

    #[test]
    fn label_column_keeps_fixed_width_left_of_elapsed() {
        let short = session(
            "short",
            "claude",
            Value::Null,
            ("impl", "delegate"),
            ("", "lane-02"),
            "2025-12-31T23:59:30+00:00",
        );
        let long = session(
            "long",
            "claude",
            Value::Null,
            ("impl", "delegate"),
            ("", "とても長い名前を持つセッション"),
            "2025-12-31T23:59:30+00:00",
        );
        let root = state_file("root.json", Value::Null, serde_json::json!([short, long]));
        let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
            .unwrap()
            .with_timezone(&Utc);

        let lines = render_state_files(&[root], 200, now);

        assert_eq!(lines.len(), 2, "{lines:?}");
        for line in &lines {
            assert!(line.ends_with("30s · running"), "{lines:?}");
        }
        assert!(lines[0].contains("lane-02"), "{lines:?}");
        assert!(lines[1].contains('…'), "{lines:?}");
        assert!(!lines[1].contains("持つセッション"), "{lines:?}");
        assert!(lines.iter().all(|line| display_width(line) <= 200));
    }

    #[test]
    fn sessions_with_non_string_last_action_are_ignored() {
        let mut broken = session(
            "broken",
            "claude",
            Value::Null,
            ("impl", "delegate"),
            ("", "fallback label"),
            "2025-12-31T23:59:30+00:00",
        );
        broken["last_action"] = Value::from(1);
        let root = state_file("root.json", Value::Null, serde_json::json!([broken]));
        let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
            .unwrap()
            .with_timezone(&Utc);

        assert!(render_state_files(&[root], 200, now).is_empty());
    }

    #[test]
    fn read_state_files_nests_writer_named_file_under_parent_thread() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let directory = std::env::temp_dir().join(format!(
            "claude-statusline-writer-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir_all(directory.join("hosts")).unwrap();
        let root = serde_json::json!({
            "version": 1,
            "host_session_id": null,
            "updated_at": "2026-01-01T00:00:00+00:00",
            "sessions": [session("parent-thread", "claude", Value::Null, ("root", "delegate"), ("", "root"), "2025-12-31T23:59:00+00:00")],
        });
        let inner = serde_json::json!({
            "version": 1,
            "host_session_id": "parent-thread",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "sessions": [
                session("delegate-session", "codex", Value::Null, ("delegate", "delegate"), ("", "delegate"), "2025-12-31T23:59:10+00:00"),
                session("explore-session", "codex", Value::Null, ("explore", "explore"), ("", "explore"), "2025-12-31T23:59:11+00:00"),
                session("shell-session", "codex", Value::Null, ("shell", "shell"), ("", "shell"), "2025-12-31T23:59:12+00:00"),
            ],
        });
        let grandchild = serde_json::json!({
            "version": 1,
            "host_session_id": "delegate-session",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "sessions": [session("grandchild", "claude", Value::Null, ("review", "delegate"), ("", "grandchild"), "2025-12-31T23:59:20+00:00")],
        });
        fs::write(directory.join("root.json"), root.to_string()).unwrap();
        fs::write(directory.join("writer.json"), inner.to_string()).unwrap();
        fs::write(directory.join("grandchild.json"), grandchild.to_string()).unwrap();

        let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
            .unwrap()
            .with_timezone(&Utc);
        let lines = render_state_files(&read_state_files(&directory), 100, now);

        assert!(lines[0].starts_with("root (Claude)"));
        assert!(lines[1].starts_with("└ delegate (Codex)"));
        assert!(lines[2].starts_with("  └ review (Claude)"));
        assert!(lines[3].starts_with("└ explore (Codex)"));
        assert!(lines[4].starts_with("└ shell (Codex)"));
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn invalid_versions_and_incomplete_sessions_are_ignored() {
        let wrong_version = serde_json::json!({
            "version": 2,
            "host_session_id": null,
            "updated_at": "2026-01-01T00:00:00+00:00",
            "sessions": [],
        });
        assert!(parse_state_file("root.json".to_string(), &wrong_version, Utc::now()).is_none());

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
    fn stale_heartbeat_files_are_hidden() {
        let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
            .unwrap()
            .with_timezone(&Utc);
        let stale = serde_json::json!({
            "version": 1,
            "host_session_id": null,
            "heartbeat_at": "2025-12-31T23:57:59+00:00",
            "updated_at": "2025-12-31T23:57:59+00:00",
            "sessions": [],
        });
        let invalid = serde_json::json!({
            "version": 1,
            "host_session_id": null,
            "heartbeat_at": "invalid",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "sessions": [],
        });

        assert!(parse_state_file("stale.json".to_string(), &stale, now).is_none());
        assert!(parse_state_file("invalid.json".to_string(), &invalid, now).is_none());
    }

    #[test]
    fn files_without_heartbeat_are_shown() {
        let file = state_file("root.json", Value::Null, serde_json::json!([]));

        assert_eq!(file.file_name, "root.json");
    }

    #[test]
    fn terminal_sessions_require_result_files() {
        let mut file = state_file(
            "root.json",
            Value::Null,
            serde_json::json!([
                session(
                    "running",
                    "claude",
                    Value::Null,
                    ("unused", "shell"),
                    ("", "running"),
                    "2025-12-31T23:59:30+00:00"
                ),
                session(
                    "collected",
                    "claude",
                    Value::Null,
                    ("unused", "shell"),
                    ("", "collected"),
                    "2025-12-31T23:59:31+00:00"
                ),
                session(
                    "retained",
                    "claude",
                    Value::Null,
                    ("unused", "shell"),
                    ("", "retained"),
                    "2025-12-31T23:59:32+00:00"
                )
            ]),
        );
        file.sessions[1].status = "completed".to_string();
        file.sessions[2].status = "failed".to_string();

        retain_sessions_with_results(&mut file, |session_id| session_id == "retained");

        assert_eq!(
            file.sessions
                .iter()
                .map(|session| session.session_id.as_str())
                .collect::<Vec<_>>(),
            ["running", "retained"]
        );
    }

    #[test]
    fn atomic_write_temporary_files_are_not_state_files() {
        assert!(is_state_file(Path::new("root.json")));
        assert!(is_state_file(Path::new("host-session.json")));
        assert!(!is_state_file(Path::new(".root.json.random.tmp")));
    }
}
