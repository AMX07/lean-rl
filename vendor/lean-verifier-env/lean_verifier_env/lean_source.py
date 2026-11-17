"""Lexical masking for policy checks; preserves offsets and newlines."""

def strip_lean_comments_and_strings(code: str) -> str:
    """Blank comments and strings while preserving token boundaries and newlines."""
    out: list[str] = []
    index = 0
    block_depth = 0
    in_string = False
    while index < len(code):
        pair = code[index:index + 2]
        char = code[index]
        if block_depth:
            if pair == "/-":
                block_depth += 1
                out.extend("  ")
                index += 2
            elif pair == "-/":
                block_depth -= 1
                out.extend("  ")
                index += 2
            else:
                out.append("\n" if char == "\n" else " ")
                index += 1
        elif in_string:
            if char == "\\" and index + 1 < len(code):
                out.extend("  ")
                index += 2
            else:
                if char == '"':
                    in_string = False
                out.append("\n" if char == "\n" else " ")
                index += 1
        elif pair == "/-":
            block_depth = 1
            out.extend("  ")
            index += 2
        elif pair == "--":
            while index < len(code) and code[index] != "\n":
                out.append(" ")
                index += 1
        elif char == '"':
            in_string = True
            out.append(" ")
            index += 1
        else:
            out.append(char)
            index += 1
    return "".join(out)

