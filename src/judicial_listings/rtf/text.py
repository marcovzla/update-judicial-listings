"""Shared RTF helpers: text codec and table-structure location.

Used by both the extractor and the editor so the two stay in lockstep.

  * `escape_text` — plain text -> RTF run text (the editor's encoder): escapes
    `\\ { }` and emits non-ASCII as `\\uN` with a one-character fallback.
  * `decode_cells` — a table body's RTF -> the visible text of each cell (the
    extractor's decoder): resolves `\\uN`, `\\'hh`, Word special-character control
    words (`\\rquote`, `\\~`, ...) and ignores formatting and source newlines.
  * `find_table_body` — locate the editable span of the Nth judge table between
    its #TableB / #TableE markers (the only region either tool may touch).

Keeping codec + structure together makes the round-trip (extract -> edit ->
re-emit) easy to reason about and test.
"""

from __future__ import annotations

from contextlib import suppress
from typing import NewType

TableOccurrence = NewType("TableOccurrence", int)
RtfOffset = NewType("RtfOffset", int)

# --- structure: locate a judge table's editable body --------------------------


def _find_real_marker(rtf: str, marker: str, occurrence: TableOccurrence) -> RtfOffset:
    """Nth real occurrence of `marker`, skipping stylesheet `marker;` definitions."""
    pos = 0
    count = 0
    while True:
        idx = rtf.find(marker, pos)
        if idx == -1:
            raise RuntimeError(f"only {count} real {marker}, asked for {occurrence}")
        if rtf[idx + len(marker) : idx + len(marker) + 1] == ";":
            pos = idx + 1
            continue
        count += 1
        if count == occurrence:
            return RtfOffset(idx)
        pos = idx + 1


def find_table_body(
    rtf: str, occurrence: TableOccurrence
) -> tuple[RtfOffset, RtfOffset]:
    """Span of the table body between the #TableB and #TableE marker paragraphs.

    `start` is just after the #TableB marker paragraph's closing brace; `end` is
    just after the last row terminator (`\\row }`) before #TableE. Row ordering
    varies (the row-properties block may precede the cells, follow them, or be
    duplicated), so the span is anchored on the markers, not on `\\trowd`.

    Fails loudly if the expected structure is missing, rather than returning a
    bogus span (e.g. (0, 0)) that would corrupt the document.
    """
    tb = _find_real_marker(rtf, "#TableB", occurrence)
    te = _find_real_marker(rtf, "#TableE", occurrence)

    par = rtf.find("\\par", tb, te)
    if par == -1:
        raise RuntimeError(f"table {occurrence}: no \\par after #TableB")
    close = rtf.find("}", par, te)
    if close == -1:
        raise RuntimeError(
            f"table {occurrence}: no closing brace after #TableB paragraph"
        )
    start = close + 1

    last_row = rtf.rfind("\\row ", start, te)
    if last_row == -1:
        raise RuntimeError(f"table {occurrence}: no \\row before #TableE")
    end_brace = rtf.find("}", last_row, te)
    if end_brace == -1:
        raise RuntimeError(f"table {occurrence}: no closing brace after last \\row")
    return RtfOffset(start), RtfOffset(end_brace + 1)


# --- encode: plain text -> RTF -------------------------------------------------


def _signed_16bit(value: int) -> int:
    return value - 0x10000 if value >= 0x8000 else value


def escape_text(text: str, ascii_fallback: str = "?") -> str:
    """Escape plain text so it can be inserted into an RTF run.

    Assumes the active \\uc count is 1 at the insertion point (true for this
    Word document), so each \\uN is followed by exactly one fallback character.
    """
    chunks: list[str] = []
    for char in text:
        if char == "\\":
            chunks.append("\\\\")
        elif char == "{":
            chunks.append("\\{")
        elif char == "}":
            chunks.append("\\}")
        elif char == "\n":
            chunks.append("\\par\n")
        elif char == "\t":
            chunks.append("\\tab ")
        else:
            code = ord(char)
            if 0x20 <= code <= 0x7E:
                chunks.append(char)
            elif code <= 0xFFFF:
                chunks.append(f"\\u{_signed_16bit(code)}{ascii_fallback}")
            else:
                value = code - 0x10000
                high = 0xD800 + (value >> 10)
                low = 0xDC00 + (value & 0x3FF)
                chunks.append(
                    f"\\u{_signed_16bit(high)}{ascii_fallback}\\u{_signed_16bit(low)}{ascii_fallback}"
                )
    return "".join(chunks)


# --- decode: RTF table body -> cell text --------------------------------------

# Word control words that stand for a literal character.
SPECIAL_WORDS = {
    "rquote": "\u2019",
    "lquote": "\u2018",
    "rdblquote": "\u201d",
    "ldblquote": "\u201c",
    "emdash": "\u2014",
    "endash": "\u2013",
    "bullet": "\u2022",
    "tab": "\t",
    "enspace": " ",
    "emspace": " ",
}


def _skip_fallback_units(body: str, pos: int, count: int) -> int:
    """Skip `count` \\ucN fallback units after a \\uN (a control token or one char)."""
    n = len(body)
    for _ in range(count):
        if pos >= n:
            break
        if body[pos] == "\\":
            if pos + 1 < n and body[pos + 1].isalpha():
                p = pos + 1
                while p < n and body[p].isalpha():
                    p += 1
                if p < n and body[p] == "-":
                    p += 1
                while p < n and body[p].isdigit():
                    p += 1
                if p < n and body[p] == " ":
                    p += 1
                pos = p
            elif pos + 1 < n and body[pos + 1] == "'":
                pos += 4
            else:
                pos += 2
        else:
            pos += 1
    return pos


def decode_cells(body: str) -> list[str]:
    """Return the visible text of each table cell in a table body, in order."""
    cells: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(body)
    uc = 1
    while i < n:
        ch = body[i]
        if ch == "{":
            # Skip ignorable destinations {\* ... } entirely.
            if body[i + 1 : i + 3] == "\\*":
                depth = 1
                i += 1
                while i < n and depth > 0:
                    if body[i] == "\\":
                        i += 2
                        continue
                    if body[i] == "{":
                        depth += 1
                    elif body[i] == "}":
                        depth -= 1
                    i += 1
                continue
            i += 1
            continue
        if ch == "}":
            i += 1
            continue
        if ch == "\\":
            nxt = body[i + 1] if i + 1 < n else ""
            if nxt.isalpha():
                j = i + 1
                while j < n and body[j].isalpha():
                    j += 1
                name = body[i + 1 : j]
                k = j
                if k < n and body[k] == "-":
                    k += 1
                while k < n and body[k].isdigit():
                    k += 1
                param = body[j:k]
                if k < n and body[k] == " ":
                    k += 1
                if name == "cell":
                    cells.append("".join(buf).strip())
                    buf = []
                elif name == "u":
                    try:
                        val = int(param)
                        if val < 0:
                            val += 65536
                        buf.append(chr(val))
                    except ValueError:
                        pass
                    k = _skip_fallback_units(body, k, uc)
                elif name == "uc":
                    with suppress(ValueError):
                        uc = int(param)
                elif name in SPECIAL_WORDS:
                    buf.append(SPECIAL_WORDS[name])
                elif name == "par":
                    buf.append(" ")
                # other control words are formatting; ignore
                i = k
                continue
            if nxt == "'":
                with suppress(ValueError):
                    buf.append(
                        bytes([int(body[i + 2 : i + 4], 16)]).decode(
                            "cp1252", "replace"
                        )
                    )
                i += 4
                continue
            if nxt == "~":
                buf.append(" ")
                i += 2
                continue
            if nxt == "_":
                buf.append("\u2011")
                i += 2
                continue
            if nxt == "-":  # optional hyphen: invisible
                i += 2
                continue
            if nxt in "{}\\":
                buf.append(nxt)
                i += 2
                continue
            i += 2  # other control symbol
            continue
        if ch in "\r\n":
            # Raw CR/LF in RTF source is insignificant whitespace (line wrapping).
            i += 1
            continue
        buf.append(ch)
        i += 1
    return cells
