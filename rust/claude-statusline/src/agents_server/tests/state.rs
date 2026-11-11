use super::*;

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
fn state_contract_fixture_matches_reader() {
    let contract = serde_json::from_str::<Value>(STATE_CONTRACT).unwrap();
    assert_eq!(contract["version"].as_u64(), Some(STATE_VERSION));
    assert_eq!(
        contract["heartbeat_expiry_seconds"].as_i64(),
        Some(HEARTBEAT_EXPIRY_SECONDS)
    );

    let example = &contract["example"];
    assert_eq!(
        sorted_keys(example),
        string_list(&contract["state_file_keys"])
    );
    assert_eq!(
        sorted_keys(&example["sessions"][0]),
        string_list(&contract["session_keys"])
    );
    let heartbeat = DateTime::parse_from_rfc3339(example["heartbeat_at"].as_str().unwrap())
        .unwrap()
        .with_timezone(&Utc);
    let parsed = parse_state_file("root.json".to_string(), example, heartbeat).unwrap();
    assert_eq!(parsed.sessions.len(), 1);
    let expired = heartbeat + chrono::Duration::seconds(HEARTBEAT_EXPIRY_SECONDS + 1);
    assert!(parse_state_file("root.json".to_string(), example, expired).is_none());

    let mut expected = PathBuf::from("/state");
    for component in contract["state_directory"].as_array().unwrap() {
        let component = component.as_str().unwrap();
        expected.push(if component == "<root_session_id>" {
            "root-1"
        } else {
            component
        });
    }
    assert_eq!(
        state_directory("linux", env(&[("XDG_STATE_HOME", "/state")]), "root-1"),
        Some(expected)
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
fn atomic_write_temporary_files_are_not_state_files() {
    assert!(is_state_file(Path::new("root.json")));
    assert!(is_state_file(Path::new("host-session.json")));
    assert!(!is_state_file(Path::new(".root.json.random.tmp")));
}
