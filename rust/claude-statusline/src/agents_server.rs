//! agents_serverが出力した状態ファイルをClaude Codeのstatusline行へ変換する。
//!
//! 状態ディレクトリはPython側の`platformdirs.user_state_dir("agent-toolkit")`と合わせる。
//! Linuxは絶対パスの`XDG_STATE_HOME`を優先し、無ければ`HOME/.local/state`、
//! Windowsは`LOCALAPPDATA`の配下に`agent-toolkit`を結合する。

use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};
use serde_json::{Map, Value};

use crate::subagent::{
    display_width, format_elapsed, normalize_description, render_line, DEFAULT_COLUMNS,
};

const STATE_VERSION: u64 = 1;
const HEARTBEAT_EXPIRY_SECONDS: i64 = 120;
// Claude Codeの描画は先頭の字下げ2セルと行末の2セルを確保する。
// 確保幅は描画された行の表示幅とCOLUMNSの差から導出した。
const STATUSLINE_RESERVED_COLUMNS: usize = 4;

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
    effort: Option<String>,
    fast_mode: Option<bool>,
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
    /// Claude Codeの利用上限の解除待ちだけが持つ、利用枠の種類と解除予定時刻。
    limit_type: Option<String>,
    resets_at: Option<String>,
}

/// `api_error.type`のうち、Claude Codeの利用上限の解除待ちを表す値。
const USAGE_LIMIT_ERROR_TYPE: &str = "usage_limit";

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
    let mut files = paths
        .into_iter()
        .filter_map(|path| {
            let file_name = path.file_name()?.to_str()?.to_string();
            let raw = fs::read_to_string(path).ok()?;
            let value = serde_json::from_str::<Value>(&raw).ok()?;
            parse_state_file(file_name, &value, Utc::now())
        })
        .collect::<Vec<_>>();
    retain_displayed_sessions(&mut files, |session_id| {
        directory
            .join("results")
            .join(format!("{session_id}.json"))
            .is_file()
    });
    files
}

/// 終端sessionは未回収の結果があるか、稼働中の子孫へつながる場合だけ残す。
///
/// 子孫の判定は全状態ファイルの親子関係を解決した後に行う。
/// 親自身の結果回収だけで行を除くと、稼働中の孫が親を失った行として残る。
fn retain_displayed_sessions(files: &mut [StateFile], mut result_exists: impl FnMut(&str) -> bool) {
    let live_hosts = live_descendant_hosts(files);
    for file in files.iter_mut() {
        file.sessions.retain(|session| {
            !matches!(
                session.status.as_str(),
                "completed" | "failed" | "interrupted"
            ) || live_hosts.contains(&session.session_id)
                || result_exists(&session.session_id)
        });
    }
}

/// 稼働中のsessionを子孫に持つsession識別子を返す。
///
/// 状態ファイルの`host_session_id`はそのファイルのsessionを起動した親を指す。
/// 生存の印が失効したファイルは解釈の段階で除かれ、稼働の根拠にならない。
fn live_descendant_hosts(files: &[StateFile]) -> HashSet<String> {
    let mut live = HashSet::new();
    loop {
        let mut changed = false;
        for file in files {
            let Some(host) = file.host_session_id.as_deref() else {
                continue;
            };
            if !live.contains(host)
                && file.sessions.iter().any(|session| {
                    session.status == "running" || live.contains(&session.session_id)
                })
            {
                live.insert(host.to_string());
                changed = true;
            }
        }
        if !changed {
            return live;
        }
    }
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
    let effort = optional_string(object, "effort")?;
    required_string(object, "model_type")?;
    let session = Session {
        session_id: required_string(object, "session_id")?,
        engine: required_string(object, "engine")?,
        model,
        effort,
        fast_mode: object.get("fast_mode").and_then(Value::as_bool),
        launch_kind: required_string(object, "launch_kind")?,
        status: required_string(object, "status")?,
        progress: required_string(object, "progress")?,
        // `last_action`は任意項目とする。長命なMCPサーバープロセスは起動時に読み込んだ
        // モジュールを保持し続けるため、statuslineだけが先に更新される間は
        // この項目がない状態ファイルが書き込まれ続ける。必須項目にすると、その間は行を表示できない。
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
    let optional_string = |key: &str| {
        object
            .get(key)
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(ToString::to_string)
    };
    Some(ApiError {
        error_type: error_type.to_string(),
        http_status,
        first_at: first_at.to_string(),
        count,
        limit_type: optional_string("limit_type"),
        resets_at: optional_string("resets_at"),
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
                    "利用上限の解除待ち {}",
                    usage_limit.limit_type.as_deref().unwrap_or("?")
                )
            } else if let Some(api_error) = api_error {
                format!("API再試行 {}", api_error.error_type)
            } else if item.session.last_action.is_empty() {
                item.session.progress.clone()
            } else {
                item.session.last_action.clone()
            };
            let mut right_parts = Vec::new();
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
    fn speed_suffix_follows_codex_state_and_preserves_legacy_rows() {
        for (engine, fast, effort, expected) in [
            (
                "codex",
                Some(true),
                Some("medium"),
                "label (codex:model/medium@fast)",
            ),
            ("codex", Some(true), None, "label (codex:model@fast)"),
            (
                "codex",
                Some(false),
                Some("medium"),
                "label (codex:model/medium)",
            ),
            ("codex", None, Some("medium"), "label (codex:model/medium)"),
            (
                "claude",
                Some(true),
                Some("medium"),
                "label (claude:model/medium)",
            ),
            (
                "agy",
                Some(true),
                Some("medium"),
                "label (agy:model/medium)",
            ),
        ] {
            let mut row = session(
                "speed",
                engine,
                Value::String("model".into()),
                ("high_tier", "delegate"),
                ("", "label"),
                "2025-12-31T23:59:30+00:00",
            );
            row["effort"] = serde_json::json!(effort);
            if let Some(enabled) = fast {
                row["fast_mode"] = serde_json::json!(enabled);
            }
            let parsed = parse_session(&row).unwrap();
            assert_eq!(display_name(&parsed, 0), expected);
            let file = state_file("root.json", Value::Null, serde_json::json!([row]));
            let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
                .unwrap()
                .with_timezone(&Utc);
            let rendered = render_state_files(&[file], DEFAULT_COLUMNS, now);
            assert_eq!(rendered.len(), 1);
            assert!(rendered[0].contains(expected));
        }
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
        let lines = render_state_files(&[grandchild, nested, orphan, root], 140, now);

        assert_eq!(lines.len(), 5);
        assert!(lines[0].starts_with("implementation label (claude:claude-opus-4-8/high)"));
        assert_eq!(lines[0].matches("implementation label").count(), 1);
        assert!(lines[0].ends_with("45s · running"));
        assert!(lines[1].starts_with("└ fallback label (codex:gpt-5.6-terra/high)"));
        assert!(lines[1].contains("latest progress"));
        assert!(lines[2].starts_with("  └ grandchild label (claude:claude-sonnet-4-6/high)"));
        assert!(lines[2].contains("grandchild label"));
        assert!(lines[3].starts_with("shell label (claude)"));
        assert!(lines[4].starts_with("└ orphan label (claude)"));
        assert!(lines.iter().all(|line| display_width(line) <= 140));
    }

    #[test]
    fn rendering_uses_configured_model_format_and_shows_label_once() {
        let mut selected = session(
            "selected",
            "codex",
            Value::String("gpt-5.6-terra".to_string()),
            ("medium_tier", "delegate"),
            ("", "pick-wi"),
            "2025-12-31T23:59:30+00:00",
        );
        selected["effort"] = Value::String("medium".to_string());
        let root = state_file("root.json", Value::Null, serde_json::json!([selected]));
        let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
            .unwrap()
            .with_timezone(&Utc);

        let lines = render_state_files(&[root], 140, now);

        assert_eq!(lines.len(), 1);
        assert!(lines[0].starts_with("pick-wi (codex:gpt-5.6-terra/medium)"));
        assert_eq!(lines[0].matches("pick-wi").count(), 1);
        assert!(lines[0].ends_with("30s · running"));
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
        let mut overloaded = retrying.clone();
        overloaded["engine"] = Value::String("codex".to_string());
        overloaded["api_error"] = serde_json::json!({
            "type": "serverOverloaded",
            "http_status": null,
            "first_at": "2025-12-31T23:59:30+00:00",
            "count": 2,
        });
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
            state_file(
                "overloaded.json",
                Value::Null,
                serde_json::json!([overloaded]),
            ),
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
        let overloaded_line = lines
            .iter()
            .find(|line| line.contains("serverOverloaded"))
            .expect("Codexの過負荷の待機を表示する");
        assert!(overloaded_line.contains("API再試行 serverOverloaded"));
        assert!(overloaded_line.contains("HTTP ?"));
        assert!(overloaded_line.contains("2回"));
        let others: Vec<_> = lines[1..]
            .iter()
            .filter(|line| !line.contains("serverOverloaded"))
            .collect();
        assert_eq!(others.len(), 3);
        assert!(others.iter().all(|line| !line.contains("API再試行")));
        assert!(others.iter().all(|line| line.contains("Bash")));
    }

    #[test]
    fn rendering_usage_limit_wait_differs_from_api_retry() {
        let mut waiting = session(
            "waiting",
            "claude",
            Value::Null,
            ("impl", "delegate"),
            ("latest progress", "lane-01"),
            "2025-12-31T23:00:00+00:00",
        );
        waiting["api_error"] = serde_json::json!({
            "type": "usage_limit",
            "http_status": 429,
            "first_at": "2025-12-31T23:30:00+00:00",
            "count": 2,
            "limit_type": "seven_day",
            "resets_at": "2026-01-01T02:05:00+00:00",
        });
        let mut unknown_reset = waiting.clone();
        unknown_reset["api_error"]["limit_type"] = Value::String("five_hour".to_string());
        unknown_reset["api_error"]
            .as_object_mut()
            .unwrap()
            .remove("resets_at");
        let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
            .unwrap()
            .with_timezone(&Utc);

        let files = [
            state_file("waiting.json", Value::Null, serde_json::json!([waiting])),
            state_file(
                "unknown.json",
                Value::Null,
                serde_json::json!([unknown_reset]),
            ),
        ];
        let lines = render_state_files(&files, 100, now);

        assert_eq!(lines.len(), 2);
        let waiting_line = lines
            .iter()
            .find(|line| line.contains("seven_day"))
            .expect("Weekly limitの解除待ちを表示する");
        assert!(
            waiting_line.contains("利用上限の解除待ち seven_day"),
            "{lines:?}"
        );
        assert!(waiting_line.contains("解除まで2h5m"), "{lines:?}");
        assert!(waiting_line.contains("30m0s"), "{lines:?}");
        assert!(!waiting_line.contains("API再試行"), "{lines:?}");
        let unknown_line = lines
            .iter()
            .find(|line| line.contains("five_hour"))
            .expect("5時間の利用上限の解除待ちを表示する");
        assert!(unknown_line.contains("解除時刻確認中"), "{lines:?}");
        assert!(lines.iter().all(|line| display_width(line) <= 100));
    }

    #[test]
    fn label_on_left_keeps_elapsed_at_right() {
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
        assert!(lines[0].starts_with("lane-02 (claude)"), "{lines:?}");
        assert!(
            lines[1].starts_with("とても長い名前を持つセッション (claude)"),
            "{lines:?}"
        );
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

        assert!(lines[0].starts_with("root (claude)"));
        assert!(lines[1].starts_with("└ delegate (codex)"));
        assert!(lines[2].starts_with("  └ grandchild (claude)"));
        assert!(lines[3].starts_with("└ explore (codex)"));
        assert!(lines[4].starts_with("└ shell (codex)"));
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

        let mut files = [file];
        retain_displayed_sessions(&mut files, |session_id| session_id == "retained");

        assert_eq!(
            files[0]
                .sessions
                .iter()
                .map(|session| session.session_id.as_str())
                .collect::<Vec<_>>(),
            ["running", "retained"]
        );
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

    #[test]
    fn terminal_parent_stays_while_grandchild_runs() {
        let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
            .unwrap()
            .with_timezone(&Utc);
        for status in ["completed", "failed", "interrupted"] {
            let parent = || {
                state_file(
                    "root.json",
                    Value::Null,
                    serde_json::json!([status_session(
                        "parent",
                        status,
                        "2025-12-31T23:59:00+00:00"
                    )]),
                )
            };
            let grandchild = |grandchild_status: &str| {
                state_file(
                    "parent-writer.json",
                    Value::String("parent".to_string()),
                    serde_json::json!([status_session(
                        "grandchild",
                        grandchild_status,
                        "2025-12-31T23:59:30+00:00"
                    )]),
                )
            };

            // 親の結果は回収済みで結果ファイルが無い。
            let mut files = [parent(), grandchild("running")];
            retain_displayed_sessions(&mut files, |_| false);
            let lines = render_state_files(&files, 140, now);
            assert_eq!(lines.len(), 2, "{lines:?}");
            assert!(lines[0].starts_with("parent (claude)"), "{lines:?}");
            assert!(lines[0].ends_with(&format!("1m0s · {status}")), "{lines:?}");
            assert!(lines[1].starts_with("└ grandchild (claude)"), "{lines:?}");
            assert!(lines[1].ends_with("30s · running"), "{lines:?}");

            // 最後の孫が終端すると、親は通常の結果判定へ戻る。
            let mut files = [parent(), grandchild("completed")];
            retain_displayed_sessions(&mut files, |_| false);
            assert!(displayed_ids(&files).is_empty());
            let mut files = [parent(), grandchild("completed")];
            retain_displayed_sessions(&mut files, |session_id| session_id == "parent");
            assert_eq!(displayed_ids(&files), ["parent"]);
        }
    }

    #[test]
    fn ancestors_stay_for_partial_and_deep_descendants() {
        let root = || {
            state_file(
                "root.json",
                Value::Null,
                serde_json::json!([status_session(
                    "parent",
                    "completed",
                    "2025-12-31T23:59:00+00:00"
                )]),
            )
        };
        let children = |second: &str| {
            state_file(
                "parent-writer.json",
                Value::String("parent".to_string()),
                serde_json::json!([
                    status_session("grandchild-1", "completed", "2025-12-31T23:59:10+00:00"),
                    status_session("grandchild-2", second, "2025-12-31T23:59:20+00:00")
                ]),
            )
        };
        let deep = |status: &str| {
            state_file(
                "grandchild-writer.json",
                Value::String("grandchild-2".to_string()),
                serde_json::json!([status_session(
                    "great-grandchild",
                    status,
                    "2025-12-31T23:59:30+00:00"
                )]),
            )
        };

        let mut files = [root(), children("running")];
        retain_displayed_sessions(&mut files, |_| false);
        assert_eq!(displayed_ids(&files), ["parent", "grandchild-2"]);

        let mut files = [root(), children("completed"), deep("running")];
        retain_displayed_sessions(&mut files, |_| false);
        assert_eq!(
            displayed_ids(&files),
            ["parent", "grandchild-2", "great-grandchild"]
        );
        let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
            .unwrap()
            .with_timezone(&Utc);
        let lines = render_state_files(&files, 140, now);
        assert!(
            lines[2].starts_with("  └ great-grandchild (claude)"),
            "{lines:?}"
        );

        let mut files = [root(), children("completed"), deep("completed")];
        retain_displayed_sessions(&mut files, |_| false);
        assert!(displayed_ids(&files).is_empty());
    }

    #[test]
    fn running_descendant_with_expired_heartbeat_keeps_no_parent() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let directory = std::env::temp_dir().join(format!(
            "claude-statusline-descendant-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir_all(&directory).unwrap();
        let fresh = Utc::now().to_rfc3339();
        let stale =
            (Utc::now() - chrono::Duration::seconds(HEARTBEAT_EXPIRY_SECONDS + 1)).to_rfc3339();
        let root = serde_json::json!({
            "version": 1,
            "host_session_id": null,
            "heartbeat_at": fresh,
            "updated_at": fresh,
            "sessions": [status_session("parent", "completed", "2025-12-31T23:59:00+00:00")],
        });
        let grandchild = serde_json::json!({
            "version": 1,
            "host_session_id": "parent",
            "heartbeat_at": stale,
            "updated_at": stale,
            "sessions": [status_session("grandchild", "running", "2025-12-31T23:59:30+00:00")],
        });
        fs::write(directory.join("root.json"), root.to_string()).unwrap();
        fs::write(directory.join("parent-writer.json"), grandchild.to_string()).unwrap();

        let files = read_state_files(&directory);

        assert!(displayed_ids(&files).is_empty());
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn atomic_write_temporary_files_are_not_state_files() {
        assert!(is_state_file(Path::new("root.json")));
        assert!(is_state_file(Path::new("host-session.json")));
        assert!(!is_state_file(Path::new(".root.json.random.tmp")));
    }
}
