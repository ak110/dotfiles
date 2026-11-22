import ast
import json
import re

_JS_TOKEN = re.compile(
    r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|[A-Za-z_$][\w$]*|\S",
    re.DOTALL,
)


def javascript_tokens(text: str) -> list[re.Match[str]]:
    return [match for match in _JS_TOKEN.finditer(text) if not match[0].startswith(("//", "/*"))]


def javascript_string(token: str) -> str | None:
    if token.startswith("`"):
        if "${" in token:
            return None
        token = '"' + token[1:-1].replace('"', '\\"').replace("\n", "\\n") + '"'
    try:
        value = json.loads(token) if token.startswith('"') else ast.literal_eval(token)
    except ValueError, SyntaxError:
        return None
    return value if isinstance(value, str) else None


def command_inputs(tool: str, text: str) -> list[str]:
    return [row["command"] for row in command_input_observations(tool, text) if row["command"] is not None]


def command_input_observations(tool: str, text: str) -> list[dict[str, str | None]]:
    leaf = tool.rsplit("__", 1)[-1].rsplit(".", 1)[-1].casefold()
    if leaf != "exec":
        return [{"command": text, "input": text, "reason": None}]
    matches = javascript_tokens(text)
    tokens = [match[0] for match in matches]
    commands = []
    for index in range(len(tokens) - 3):
        if tokens[index : index + 4] != ["tools", ".", "exec_command", "("]:
            continue
        end = index + 4
        parentheses = 1
        while end < len(tokens) and parentheses:
            if tokens[end] == "(":
                parentheses += 1
            elif tokens[end] == ")":
                parentheses -= 1
            end += 1
        source = text[matches[index].start() : matches[end - 1].end()]
        observation = {"command": None, "input": source, "reason": "実行引数を静的に確定できない"}
        commands.append(observation)
        if index + 4 >= len(tokens) or tokens[index + 4] != "{":
            continue
        depth = 1
        cursor = index + 5
        ambiguous = False
        command_count = 0
        while cursor < len(tokens) and depth:
            token = tokens[cursor]
            if token == "{":
                depth += 1
            elif token == "}":
                depth -= 1
            if depth == 1 and tokens[cursor : cursor + 3] == [".", ".", "."]:
                ambiguous = True
            # 計算キー・短縮記法・アクセサーはcmdを上書きし得るため、式を評価せず操作を未確定とする。  # pyfltr: allow-comment
            if depth == 1 and token == "[":
                ambiguous = True
            if (
                depth == 1
                and (token == "cmd" or javascript_string(token) == "cmd")
                and tokens[cursor - 1] in {"{", ",", "get", "set"}
                and cursor + 1 < len(tokens)
                and tokens[cursor + 1] in {",", "}", "("}
            ):
                ambiguous = True
            if (
                depth == 1
                and (token == "cmd" or javascript_string(token) == "cmd")
                and cursor + 2 < len(tokens)
                and tokens[cursor + 1] == ":"
            ):
                value = javascript_string(tokens[cursor + 2])
                command_count += 1
                if command_count > 1:
                    ambiguous = True
                if value is not None and cursor + 3 < len(tokens) and tokens[cursor + 3] in {",", "}"}:
                    observation["command"] = value
                    observation["reason"] = None
            cursor += 1
        if cursor < len(tokens):
            observation["input"] = text[matches[index].start() : matches[cursor].end()]
        if depth or ambiguous:
            observation["command"] = None
            observation["reason"] = "実行引数の閉じ位置または上書きを静的に確定できない"
    return commands
