use super::*;

#[test]
fn shared_tree_fixture_uses_parent_ids_for_orphan_subtrees() {
    let contract = serde_json::from_str::<Value>(STATE_CONTRACT).unwrap();
    let files = contract["tree_files"]
        .as_array()
        .unwrap()
        .iter()
        .map(|file| {
            let rows = file["session_ids"]
                .as_array()
                .unwrap()
                .iter()
                .map(|id| {
                    status_session(id.as_str().unwrap(), "running", "2025-12-31T23:59:00+00:00")
                })
                .collect::<Vec<_>>();
            state_file(
                file["file_name"].as_str().unwrap(),
                file["host_session_id"].clone(),
                serde_json::json!(rows),
            )
        })
        .collect::<Vec<_>>();
    let displayed = flatten_sessions(&files);
    assert_eq!(
        displayed
            .iter()
            .map(|item| item.session.session_id.as_str())
            .collect::<Vec<_>>(),
        contract["tree_order"]
            .as_array()
            .unwrap()
            .iter()
            .map(|id| id.as_str().unwrap())
            .collect::<Vec<_>>()
    );
    assert_eq!(
        displayed.iter().map(|item| item.depth).collect::<Vec<_>>(),
        contract["tree_depths"]
            .as_array()
            .unwrap()
            .iter()
            .map(|depth| depth.as_u64().unwrap() as usize)
            .collect::<Vec<_>>()
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
        assert!(lines[0].contains("対応不要"), "{lines:?}");
        assert!(
            lines[0].contains("結果回収・子孫session終了後に非表示"),
            "{lines:?}"
        );
        assert!(lines[1].starts_with("└ grandchild (claude)"), "{lines:?}");
        assert!(lines[1].ends_with("30s · running"), "{lines:?}");

        // 最後の孫が終了すると、親は通常の結果判定へ戻る。
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
    let stale = (Utc::now() - chrono::Duration::seconds(HEARTBEAT_EXPIRY_SECONDS + 1)).to_rfc3339();
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
