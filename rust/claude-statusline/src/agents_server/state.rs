//! 状態ディレクトリの解決と状態ファイルの読込・検証。

use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};
use serde_json::{Map, Value};

use super::{ApiError, Session, StateFile, HEARTBEAT_EXPIRY_SECONDS, STATE_VERSION};

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

/// 終了sessionは未回収の結果があるか、稼働中の子孫へつながる場合だけ残す。
///
/// 子孫の判定は全状態ファイルの親子関係を解決した後に行う。
/// 親自身の結果回収だけで行を除くと、稼働中の孫が親を失った行として残る。
pub(super) fn retain_displayed_sessions(
    files: &mut [StateFile],
    mut result_exists: impl FnMut(&str) -> bool,
) {
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

pub(super) fn is_state_file(path: &Path) -> bool {
    path.extension().and_then(|extension| extension.to_str()) == Some("json")
}

pub(super) fn parse_state_file(
    file_name: String,
    value: &Value,
    now: DateTime<Utc>,
) -> Option<StateFile> {
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

pub(super) fn parse_session(value: &Value) -> Option<Session> {
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
        context_tokens: object.get("context_tokens").cloned(),
        context_window: object.get("context_window").cloned(),
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
