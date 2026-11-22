from agent_toolkit._common import background_output


def test_output_paths_strip_sentence_punctuation() -> None:
    text = (
        "Command running in background with ID: b71657cxm. Output is being written to: "
        "/tmp/claude-1000/project/session/tasks/b71657cxm.output. You will be notified when it completes."
    )

    assert background_output.output_paths(text) == ["/tmp/claude-1000/project/session/tasks/b71657cxm.output"]
    assert not background_output.output_paths("Output is being written to: relative.output")
    assert not background_output.output_paths("was moved to the background (ID: bgm3jt6xn).")
