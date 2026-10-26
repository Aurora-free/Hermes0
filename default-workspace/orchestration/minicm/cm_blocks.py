"""Faithful port of cmark 0.31.2 src/blocks.c — CommonMark block layer.

Cursor model mirrors cmark exactly: (offset=byte index, column=virtual
column with TAB_STOP=4), partially_consumed_tab, single rolling content
buffer that is detached from the parser when a block is finalized.
Scanners are ports of src/scanners.re (re2c patterns) and the reference
machinery from src/inlines.c.
"""
import re
import string

# ---------------------------------------------------------------- node types
DOC, BLOCK_QUOTE, ITEM, LIST, PARA, HEADING, CODE_BLOCK, HTML_BLOCK, THEMATIC = range(9)

CODE_INDENT = 4
TAB_STOP = 4
MAX_LINK_LABEL_LENGTH = 1000

_PUNCT_ASCII = frozenset(string.punctuation)


class Node:
    __slots__ = (
        "type", "parent", "prev", "next", "first_child", "last_child", "open",
        "start_line", "start_column", "end_line", "end_column",
        "content", "inline_html",
        "fenced", "fence_char", "fence_length", "fence_offset", "info",
        "level", "setext", "internal_offset",
        "html_block_type",
        "list_type", "bullet_char", "start_num", "list_delim", "tight",
        "marker_offset", "padding",
        "last_line_blank", "list_blank", "last_line_checked",
    )

    def __init__(self, ntype, start_line=1, start_column=1):
        self.type = ntype
        self.parent = None
        self.prev = None
        self.next = None
        self.first_child = None
        self.last_child = None
        self.open = True
        self.start_line = start_line
        self.start_column = start_column
        self.end_line = start_line
        self.end_column = 0
        self.content = ""
        self.inline_html = ""
        self.fenced = False
        self.fence_char = 0
        self.fence_length = 0
        self.fence_offset = 0
        self.info = None
        self.level = 0
        self.setext = False
        self.internal_offset = 0
        self.html_block_type = 0
        self.list_type = 0        # 1 bullet, 2 ordered
        self.bullet_char = ""
        self.start_num = 0
        self.list_delim = 0       # 1 period, 2 paren
        self.tight = False
        self.marker_offset = 0
        self.padding = 0
        self.last_line_blank = False
        self.list_blank = False
        self.last_line_checked = False

    def is_last_child_open(self):
        return self.last_child is not None and self.last_child.open


# ---------------------------------------------------------------- scanners
_BLOCKTAGS = (
    "address|article|aside|base|basefont|blockquote|body|caption|center|"
    "col|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|"
    "footer|form|frame|frameset|h1|h2|h3|h4|h5|h6|head|header|hr|html|iframe|"
    "legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|"
    "param|section|search|summary|table|tbody|td|tfoot|th|thead|tr|track|ul"
)
_SP = r"[\t\n\v\f\r ]"
_ATTR_NAME = r"[a-zA-Z_:][a-zA-Z0-9:._-]*"
_UNQ = (r"""[^ \t\r\n\v\f"'=<>`]+"""
        r"""|\'[^\x00']*\'"""
        r"""|\"[^\x00\"]*\"""")
_ATTR_SPEC = _SP + r"?" + r"=" + _SP + r"?" + "(" + _UNQ + ")"
_ATTR = _SP + r"+" + _ATTR_NAME + "(" + _ATTR_SPEC + ")?"
_OPEN_TAG = r"[A-Za-z][A-Za-z0-9-]*(" + _ATTR + r")*" + _SP + r"?" + r"(?:/)?[>]"
_CLOSE_TAG = r"/[A-Za-z][A-Za-z0-9-]*" + _SP + r"*[>]"
_T7_TRAIL = r"[\t\n\f ]*[\r\n]"

_RE_HTML1 = re.compile(r"<(script|pre|textarea|style)([\t\n\v\f\r ]|>)")
_RE_HTML6 = re.compile(r"</?(?:%s)([\t\n\v\f\r ]|/?>)" % _BLOCKTAGS)
_RE_HTML7_OPEN = re.compile(r"<" + _OPEN_TAG + _T7_TRAIL)
_RE_HTML7_CLOSE = re.compile(r"<" + _CLOSE_TAG + _T7_TRAIL)

_RE_ATX = re.compile(r"#{1,6}([ \t]+|[\r\n])")
_RE_SETEXT1 = re.compile(r"=+[ \t]*[\r\n]")
_RE_SETEXT2 = re.compile(r"-+[ \t]*[\r\n]")


def _peek(s, i):
    return s[i] if i < len(s) else "\0"


def scan_atx(line, first):
    """_scan_atx_heading_start: matched length incl. trailing space/eol, or 0."""
    m = _RE_ATX.match(line, first)
    return m.end() - first if m else 0


def scan_open_code_fence(line, first):
    """_scan_open_code_fence: number of fence chars, or 0."""
    c = _peek(line, first)
    if c not in "`~":
        return 0
    j = first
    n = len(line)
    while j < n and line[j] == c:
        j += 1
    run = j - first
    if run < 3:
        return 0
    rest = line[j:]
    nl = rest.find("\n")
    body = rest[:nl] if nl >= 0 else rest
    if c == "`" and "`" in body:
        return 0
    if not rest.endswith(("\n", "\r\n")):
        return 0
    return run


def scan_close_code_fence(line, first):
    """_scan_close_code_fence: fence length, or 0."""
    c = _peek(line, first)
    if c not in "`~":
        return 0
    j = first
    n = len(line)
    while j < n and line[j] == c:
        j += 1
    run = j - first
    if run < 3:
        return 0
    k = j
    while k < n and line[k] in " \t":
        k += 1
    if k < n and line[k] == "\r":
        k += 1
    if k < n and line[k] == "\n":
        return run
    return 0


def scan_setext(line, first):
    """_scan_setext_heading_line: 1 for '=', 2 for '-', else 0."""
    if _RE_SETEXT1.match(line, first):
        return 1
    if _RE_SETEXT2.match(line, first):
        return 2
    return 0


def scan_thematic_break(parser, line, first):
    """S_scan_thematic_break; sets parser.thematic_break_kill_pos on failure."""
    s = line
    i = first
    c = _peek(s, i)
    if c not in "*_-":
        parser.thematic_break_kill_pos = i
        return 0
    count = 1
    i += 1
    n = len(s)
    nextc = "\0"
    while i < n:
        nextc = s[i]
        if nextc == c:
            count += 1
            i += 1
        elif nextc in " \t":
            i += 1
        else:
            break
    if count >= 3 and (i >= n or s[i] in "\r\n"):
        return i - first + 1
    parser.thematic_break_kill_pos = i
    return 0


def scan_html_block_start(line, first):
    """_scan_html_block_start: type 1-6, or 0."""
    s = line
    n = len(s)
    if first >= n or s[first] != "<":
        return 0
    if _RE_HTML1.match(s, first):
        return 1
    if s[first:first + 4] == "<!--":
        return 2
    if s[first:first + 2] == "<?":
        return 3
    if s[first + 1] == "!" and _peek(s, first + 2).isalpha() and _peek(s, first + 2).isascii():
        return 4
    if s[first:first + 9] == "<![CDATA[":
        return 5
    if _RE_HTML6.match(s, first):
        return 6
    return 0


def scan_html_block_start_7(line, first):
    """_scan_html_block_start_7: full tag line -> 7, else 0."""
    s = line
    if first >= len(s) or s[first] != "<":
        return 0
    if _RE_HTML7_OPEN.match(s, first) or _RE_HTML7_CLOSE.match(s, first):
        return 7
    return 0


def scan_html_block_end(line, first, block_type):
    """_scan_html_block_end_1..5: 1 if the line matches the end condition."""
    s = line
    tail = s[first:]
    if block_type == 1:
        return 1 if re.search(r"</(?:script|pre|textarea|style)>", tail) else 0
    if block_type == 2:
        return 1 if "-->" in tail else 0
    if block_type == 3:
        return 1 if "?>" in tail else 0
    if block_type == 4:
        return 1 if ">" in tail else 0
    if block_type == 5:
        return 1 if "]]>" in tail else 0
    return 0


def parse_list_marker(line, pos, interrupts_paragraph):
    """parse_list_marker: matched byte length + data tuple, or (0, None)."""
    s = line
    n = len(s)
    c = _peek(s, pos)
    startpos = pos
    data = None
    if c in "*-+":
        pos += 1
        if not cmark_isspace(_peek(s, pos)):
            return 0, None
        if interrupts_paragraph:
            i = pos
            while i < n and s[i] in " \t":
                i += 1
            if _peek(s, i) == "\n":
                return 0, None
        data = {"marker_offset": 0, "list_type": 1, "bullet_char": c,
                "start": 0, "list_delim": 0}
    elif c.isdigit():
        i = pos
        start = 0
        digits = 0
        while i < n and s[i].isdigit() and digits < 9:
            start = start * 10 + (ord(s[i]) - 48)
            i += 1
            digits += 1
        if interrupts_paragraph and start != 1:
            return 0, None
        c2 = _peek(s, i)
        if c2 not in ".)":
            return 0, None
        i += 1
        pos = i  # marker width = digits + delimiter
        if not cmark_isspace(_peek(s, pos)):
            return 0, None
        if interrupts_paragraph:
            j = pos
            while j < n and s[j] in " \t":
                j += 1
            if _peek(s, j) in "\r\n":
                return 0, None
        data = {"marker_offset": 0, "list_type": 2, "bullet_char": "",
                "start": start, "list_delim": 1 if c2 == "." else 2}
    else:
        return 0, None
    return pos - startpos, data


def cmark_isspace(c):
    return c in " \t\n\v\f\r"


# ---------------------------------------------------------------- reference machinery (port of inlines.c)
import md_inlines as _mi


def _clean_url(t):
    return _mi._clean_url(t)


def _clean_title(t):
    return _mi._clean_title(t)


def normalize_reference(label):
    """Delegate to the shared cmark-faithful normalizer (Unicode case-fold)."""
    return _mi.normalize_reference(label)


def link_label(s, pos):
    """link_label: label ends with ']'. Returns (raw_label, pos_after_) or None.
    pos points AT the '[' character."""
    n = len(s)
    if pos >= n or s[pos] != "[":
        return None
    startpos = pos
    pos += 1
    length = 0
    while pos < n:
        c = s[pos]
        if c in "[]":
            break
        if c == "\\":
            pos += 1
            length += 1
            if pos < n and s[pos] in _PUNCT_ASCII:
                pos += 1
                length += 1
        else:
            pos += 1
            length += 1
        if length > MAX_LINK_LABEL_LENGTH:
            return None
    if pos < n and s[pos] == "]":
        raw = s[startpos + 1:pos]
        return raw, pos + 1
    return None


def manual_scan_link_url(s, offset):
    """cmark manual_scan_link_url: bytes consumed, or -1. For the
    <...> form, the interior excludes the angle brackets (mirror of
    cmark's chunk {data + offset + 1, i - 2 - offset})."""
    n = len(s)
    i = offset
    if i < n and s[i] == "<":
        i += 1
        while i < n:
            c = s[i]
            if c == ">":
                i += 1
                break
            elif c == "\\":
                i += 2
            elif c in "\n<":
                return -1
            else:
                i += 1
        if i >= n:
            return -1
        return i - offset
    # raw URL form (manual_scan_link_url_2): no leading-space skip
    nb = 0
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n and s[i + 1] in _PUNCT_ASCII:
            i += 2
        elif c == "(":
            nb += 1
            i += 1
            if nb > 32:
                return -1
        elif c == ")":
            if nb == 0:
                break
            nb -= 1
            i += 1
        elif cmark_isspace(c):
            if i == offset:
                return -1
            break
        else:
            i += 1
    if i >= n or nb != 0:
        return -1
    return i - offset


_RE_LINK_TITLE = re.compile(
    r"(?:\"((?:\\[\x21-\x7e]|[^\x00\"\\]|\\)*)\""
    r"|\'((?:\\[\x21-\x7e]|[^\x00\'\\]|\\)*)\'"
    r"|\(((?:\\[\x21-\x7e]|[^\x00)\\]|\\)*)\))"
)


def scan_link_title(s, pos):
    """_scan_link_title: length of title (incl. delimiters), or 0."""
    m = _RE_LINK_TITLE.match(s, pos)
    return m.end() - pos if m else 0


def parse_reference_inline(s, pos, refs):
    """cmark_parse_reference_inline: consume bytes of one refdef; 0 if none.
    mutates refs dict. pos points AT '['. Returns position AFTER the
    consumed line end (mirrors cmark's return of subj->pos)."""
    n = len(s)
    lr = link_label(s, pos)
    if lr is None:
        return 0
    label, pos = lr
    if label.strip() == "":
        return 0
    if pos >= n or s[pos] != ":":
        return 0
    pos += 1

    def skip_sp(p):
        while p < n and s[p] in " \t":
            p += 1
        return p

    def skip_newline_once(p):
        """Consume at most one line break, then spaces. Returns new p and
        whether a line break was consumed."""
        consumed = False
        if p < n and s[p] == "\r":
            p += 1
        if p < n and s[p] == "\n":
            p += 1
            consumed = True
        p = skip_sp(p)
        return p, consumed

    # skip spaces after the colon
    pos = skip_sp(pos)
    # the URL may start on the next line (at most one line break)
    pos, _ = skip_newline_once(pos)

    def skip_line_end(p):
        q = p
        if q < n and s[q] == "\r":
            q += 1
        if q < n and s[q] == "\n":
            q += 1
        return q

    def spnl(p):
        p = skip_sp(p)
        q = skip_line_end(p)
        if q != p:
            p = skip_sp(q)
        return p

    # parse the link url (spnl before: spaces, one optional line break, spaces)
    pos = spnl(pos)
    url_start = pos
    matchlen = manual_scan_link_url(s, url_start)
    if matchlen < 0:
        return 0
    if s[url_start] == "<":
        url_raw = s[url_start + 1: url_start + matchlen - 1]
    else:
        url_raw = s[url_start: url_start + matchlen]
    pos = url_start + matchlen

    # optional title: only attempted when spnl actually advanced (cmark)
    beforetitle = pos
    pt = spnl(pos)
    tlen = scan_link_title(s, pt) if pt != beforetitle else 0
    if tlen:
        title_raw = s[pt: pt + tlen]
        pos = pt + tlen
    else:
        title_raw = ""
        pos = beforetitle

    # final spaces and newline; rewind before title if that fails (cmark)
    q = skip_sp(pos)
    if skip_line_end(q) == q:
        if tlen:
            q = skip_sp(beforetitle)
            if skip_line_end(q) == q:
                return 0
        else:
            return 0
    end = skip_line_end(q)

    key = normalize_reference(label)
    if key is None:
        return 0
    url = _clean_url(url_raw.strip())
    title = _clean_title(title_raw)
    if key not in refs:
        refs[key] = (url, title)
    return end



# ---------------------------------------------------------------- parser
def _can_contain(pt, ct):
    return (
        pt == DOC
        or pt == BLOCK_QUOTE
        or pt == ITEM
        or (pt == LIST and ct == ITEM)
    )


def _accepts_lines(bt):
    return bt in (PARA, HEADING, CODE_BLOCK)


def _is_blank(s, offset):
    i = offset
    n = len(s)
    while i < n:
        c = s[i]
        if c in "\r\n":
            return True
        if c in " \t":
            i += 1
        else:
            return False
    return True


def _lists_match(list_data, item_data):
    return (
        list_data["list_type"] == item_data["list_type"]
        and list_data["list_delim"] == item_data["list_delim"]
        and list_data["bullet_char"] == item_data["bullet_char"]
    )


def _remove_trailing_blank_lines(s):
    """remove_trailing_blank_lines on the rolling content buffer."""
    i = len(s) - 1
    while i >= 0:
        c = s[i]
        if c not in " \t\r\n":
            break
        i -= 1
    if i < 0:
        return ""
    for j in range(i + 1, len(s)):
        if s[j] in "\r\n":
            return s[:j]
    return s


def _ends_with_blank_line(node, memo=None):
    """S_ends_with_blank_line (memoized)."""
    if node.last_line_checked:
        return node.last_line_blank
    if node.type in (LIST, ITEM) and node.last_child is not None:
        node.last_line_checked = True
        return _ends_with_blank_line(node.last_child)
    node.last_line_checked = True
    return node.last_line_blank


class BlockParser:
    """Port of cmark_parser + S_process_line + check_open_blocks +
    open_new_blocks + add_text_to_container + finalize."""

    def __init__(self):
        self.root = Node(DOC)
        self.root.open = True
        self.current = self.root
        self.refs = {}
        # rolling line cursor
        self.offset = 0
        self.column = 0
        self.first_nonspace = 0
        self.first_nonspace_column = 0
        self.thematic_break_kill_pos = 0
        self.indent = 0
        self.blank = False
        self.partially_consumed_tab = False
        self.line_number = 0
        # rolling content buffer (cmark parser->content)
        self._buf = []
        self._line = ""

    # ------------------------------------------------------------ feed
    def run(self, text):
        """Parse full markdown text; returns root node tree with refs."""
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if text.startswith("\ufeff"):
            text = text[1:]
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        for i, ln in enumerate(lines):
            self._process_line(ln + "\n")
        # finalize_document
        while self.current is not self.root:
            self.current = self._finalize(self.current)
        self._finalize(self.root)
        return self.root

    def _process_line(self, line):
        self._line = line
        self.offset = 0
        self.column = 0
        self.first_nonspace = 0
        self.first_nonspace_column = 0
        self.thematic_break_kill_pos = 0
        self.indent = 0
        self.blank = False
        self.partially_consumed_tab = False
        self._chop_n = None
        self._fence_closed = False
        self.line_number += 1

        last_matched, all_matched = self._check_open_blocks()
        if last_matched is None:
            return
        container = last_matched
        cref = [container]
        self._open_new_blocks(cref, all_matched)
        container = cref[0]
        self._add_text_to_container(container, last_matched)

    # ------------------------------------------------------------ cursor helpers
    def _find_first_nonspace(self):
        s = self._line
        chars_to_tab = TAB_STOP - (self.column % TAB_STOP)
        if self.first_nonspace <= self.offset:
            self.first_nonspace = self.offset
            self.first_nonspace_column = self.column
            i = self.first_nonspace
            n = len(s)
            while i < n:
                c = s[i]
                if c == " ":
                    self.first_nonspace += 1
                    self.first_nonspace_column += 1
                    chars_to_tab -= 1
                    if chars_to_tab == 0:
                        chars_to_tab = TAB_STOP
                    i += 1
                elif c == "\t":
                    self.first_nonspace += 1
                    self.first_nonspace_column += chars_to_tab
                    chars_to_tab = TAB_STOP
                    i += 1
                else:
                    break
        self.indent = self.first_nonspace_column - self.column
        self.blank = _peek(self._line, self.first_nonspace) in "\r\n\0"

    def _advance_offset(self, count, columns):
        s = self._line
        n = len(s)
        while count > 0 and self.offset < n:
            c = s[self.offset]
            if c == "\t":
                chars_to_tab = TAB_STOP - (self.column % TAB_STOP)
                if columns:
                    self.partially_consumed_tab = chars_to_tab > count
                    adv = min(count, chars_to_tab)
                    self.column += adv
                    self.offset += 0 if self.partially_consumed_tab else 1
                    count -= adv
                else:
                    self.partially_consumed_tab = False
                    self.column += chars_to_tab
                    self.offset += 1
                    count -= 1
            else:
                self.partially_consumed_tab = False
                self.offset += 1
                self.column += 1
                count -= 1

    def _add_line(self):
        """cmark add_line: append line[offset:chop_n] to rolling buffer,
        expanding a partially consumed tab into spaces first."""
        if self.partially_consumed_tab:
            self.offset += 1
            chars_to_tab = TAB_STOP - (self.column % TAB_STOP)
            self._buf.append(" " * chars_to_tab)
        s = self._line
        end = self._chop_n if self._chop_n is not None else len(s)
        end = max(end, self.offset)
        self._buf.append(s[self.offset:end])

    # ------------------------------------------------------------ check_open_blocks
    def _check_open_blocks(self):
        """Returns (last_matched_container_or_None, all_matched)."""
        s = self._line
        container = self.root
        all_matched = False
        should_continue = True
        while container.is_last_child_open():
            container = container.last_child
            t = container.type
            self._find_first_nonspace()
            if t == BLOCK_QUOTE:
                if not self._parse_block_quote_prefix():
                    break
            elif t == LIST:
                if self.blank:
                    if container.list_blank and self.indent == 0:
                        # abort early on consecutive blanks
                        if self.current.type in (CODE_BLOCK, HTML_BLOCK):
                            self._add_line()
                        return None, False
                    container.list_blank = True
                else:
                    container.list_blank = False
            elif t == ITEM:
                if not self._parse_item_prefix(container):
                    break
            elif t == CODE_BLOCK:
                if not self._parse_code_block_prefix(container):
                    break
                if self._fence_closed:
                    should_continue = False
                    break
            elif t == HEADING:
                break
            elif t == HTML_BLOCK:
                if not self._parse_html_block_prefix(container):
                    break
            elif t == PARA:
                if self.blank:
                    break
            # default: pass
        else:
            all_matched = True
        if not all_matched:
            container = container.parent
        if not should_continue:
            container = None
        return container, all_matched

    def _parse_code_block_prefix(self, container):
        """Returns True if the line prefix matched; on fence close,
        finalizes the fence and records that processing stops."""
        s = self._line
        if not container.fenced:
            if self.indent >= CODE_INDENT:
                self._advance_offset(CODE_INDENT, True)
                return True
            elif self.blank:
                self._advance_offset(self.first_nonspace - self.offset, False)
                return True
            return False
        else:
            matched = 0
            if self.indent <= 3 and _peek(s, self.first_nonspace) == container.fence_char:
                matched = scan_close_code_fence(s, self.first_nonspace)
            if matched >= container.fence_length:
                self._advance_offset(matched, False)
                self.current = self._finalize(container)
                self._fence_closed = True
                return True
            i = container.fence_offset
            while i > 0 and _peek(s, self.offset) in " \t":
                self._advance_offset(1, True)
                i -= 1
            return True

    def _current_is_open_at(self, container):
        """After _parse_code_block_prefix: True unless the fence just closed."""
        return not getattr(self, "_fence_closed", False)

    def _parse_block_quote_prefix(self):
        s = self._line
        if self.indent <= 3 and _peek(s, self.first_nonspace) == ">":
            self._advance_offset(self.indent + 1, True)
            if _peek(s, self.offset) in " \t":
                self._advance_offset(1, True)
            return True
        return False

    def _parse_item_prefix(self, container):
        if self.indent >= container.marker_offset + container.padding:
            self._advance_offset(container.marker_offset + container.padding, True)
            return True
        elif self.blank and container.first_child is not None:
            self._advance_offset(self.first_nonspace - self.offset, False)
            return True
        return False

    def _parse_html_block_prefix(self, container):
        bt = container.html_block_type
        if bt in (1, 2, 3, 4, 5):
            return True
        return not self.blank

    # ------------------------------------------------------------ open_new_blocks
    def _open_new_blocks(self, cref, all_matched):
        """cref is a single-element list holding the container node;
        mirrors cmark's `cmark_node **container` pointer parameter."""
        parser = self
        s = parser._line
        container = cref[0]
        indented = False
        maybe_lazy = parser.current.type == PARA
        cont_type = container.type
        while cont_type not in (CODE_BLOCK, HTML_BLOCK):
            parser._find_first_nonspace()
            indented = parser.indent >= CODE_INDENT
            first = parser.first_nonspace
            pc = _peek(s, first)

            if not indented and pc == ">":
                bq_start = first
                parser._advance_offset(first + 1 - parser.offset, False)
                if _peek(s, parser.offset) in " \t":
                    parser._advance_offset(1, True)
                cref[0] = parser._add_child(container, BLOCK_QUOTE, bq_start + 1)
            elif not indented and (am := scan_atx(s, first)) > 0:
                heading_start = first
                parser._advance_offset(first + am - parser.offset, False)
                cref[0] = parser._add_child(container, HEADING, heading_start + 1)
                hashpos = s.find("#", first)
                level = 0
                while hashpos < len(s) and s[hashpos] == "#":
                    level += 1
                    hashpos += 1
                cref[0].level = level
                cref[0].setext = False
                cref[0].internal_offset = am
            elif not indented and (fm := scan_open_code_fence(s, first)) > 0:
                cref[0] = parser._add_child(container, CODE_BLOCK, first + 1)
                cref[0].fenced = True
                cref[0].fence_char = s[first]
                cref[0].fence_length = min(fm, 255)
                cref[0].fence_offset = first - parser.offset
                cref[0].info = None
                parser._advance_offset(first + fm - parser.offset, False)
            elif not indented and (
                hb := (scan_html_block_start(s, first)
                       or (0 if (cont_type == PARA or maybe_lazy)
                           else scan_html_block_start_7(s, first)))
            ) > 0:
                cref[0] = parser._add_child(container, HTML_BLOCK, first + 1)
                cref[0].html_block_type = hb
            elif (
                not indented
                and cont_type == PARA
                and (lev := scan_setext(s, first)) > 0
                and parser._resolve_refdefs()
            ):
                container.type = HEADING
                container.level = lev
                container.setext = True
                container.internal_offset = 0
                parser._advance_offset(len(s) - 1 - parser.offset, False)
                cont_type = container.type
                if _accepts_lines(cont_type):
                    break
                maybe_lazy = False
                continue
            elif (
                not indented
                and not (cont_type == PARA and not all_matched)
                and parser.thematic_break_kill_pos <= first
                and scan_thematic_break(parser, s, first)
            ):
                cref[0] = parser._add_child(container, THEMATIC, first + 1)
                parser._advance_offset(len(s) - 1 - parser.offset, False)
            elif (
                (not indented or cont_type == LIST)
                and parser.indent < 4
                and (lm := parse_list_marker(s, first, container.type == PARA))[0] > 0
            ):
                ldata = lm[1]
                parser._advance_offset(first + lm[0] - parser.offset, False)
                save_pct = parser.partially_consumed_tab
                save_off = parser.offset
                save_col = parser.column
                while (parser.column - save_col <= 5 and _peek(s, parser.offset) in " \t"):
                    parser._advance_offset(1, True)
                i = parser.column - save_col
                if i >= 5 or i < 1 or _peek(s, parser.offset) in "\r\n":
                    ldata["padding"] = lm[0] + 1
                    parser.offset = save_off
                    parser.column = save_col
                    parser.partially_consumed_tab = save_pct
                    if i > 0:
                        parser._advance_offset(1, True)
                else:
                    ldata["padding"] = lm[0] + i
                ldata["marker_offset"] = parser.indent
                if cont_type != LIST or not _lists_match(
                    {
                        "list_type": container.list_type,
                        "list_delim": container.list_delim,
                        "bullet_char": container.bullet_char,
                    },
                    ldata,
                ):
                    cref[0] = parser._add_child(container, LIST, first + 1)
                    cref[0].list_type = ldata["list_type"]
                    cref[0].bullet_char = ldata["bullet_char"]
                    cref[0].start_num = ldata["start"]
                    cref[0].list_delim = ldata["list_delim"]
                cref[0] = parser._add_child(cref[0], ITEM, first + 1)
                cref[0].marker_offset = ldata["marker_offset"]
                cref[0].padding = ldata["padding"]
            elif indented and not maybe_lazy and not parser.blank:
                parser._advance_offset(CODE_INDENT, True)
                cref[0] = parser._add_child(container, CODE_BLOCK, parser.offset + 1)
                cref[0].fenced = False
                break
            else:
                break

            if _accepts_lines(cref[0].type):
                break
            container = cref[0]
            cont_type = container.type
            maybe_lazy = False

    def _add_child(self, parent, btype, start_column):
        b = Node(btype, self.line_number, start_column)
        while not _can_contain(parent.type, btype):
            parent = self._finalize(parent)
        b.parent = parent
        if parent.last_child:
            parent.last_child.next = b
            b.prev = parent.last_child
        else:
            parent.first_child = b
        parent.last_child = b
        return b

    # ------------------------------------------------------------ add_text_to_container
    def _add_text_to_container(self, container, last_matched):
        parser = self
        parser._find_first_nonspace()
        if parser.blank and container.last_child:
            container.last_child.last_line_blank = True
        ctype = container.type
        last_line_blank = (
            parser.blank
            and ctype != BLOCK_QUOTE
            and ctype != HEADING
            and ctype != THEMATIC
            and not (ctype == CODE_BLOCK and container.fenced)
            and not (
                ctype == ITEM
                and container.first_child is None
                and container.start_line == parser.line_number
            )
        )
        container.last_line_blank = last_line_blank
        tmp = container
        while tmp.parent:
            tmp.parent.last_line_blank = False
            tmp = tmp.parent
        cur = parser.current
        if (
            cur is not last_matched
            and container is last_matched
            and not parser.blank
            and cur.type == PARA
        ):
            parser._add_line()
        else:
            while cur is not last_matched:
                cur = parser._finalize(cur)
                parser.current = cur
            if container.type == CODE_BLOCK:
                parser._add_line()
            elif container.type == HTML_BLOCK:
                parser._add_line()
                if scan_html_block_end(parser._line, parser.first_nonspace, container.html_block_type):
                    container = parser._finalize(container)
            elif parser.blank:
                pass
            elif _accepts_lines(container.type):
                if container.type == HEADING and not container.setext:
                    parser._chop_trailing_hashtags()
                parser._advance_offset(parser.first_nonspace - parser.offset, False)
                parser._add_line()
            else:
                container = parser._add_child(container, PARA, parser.first_nonspace + 1)
                parser._advance_offset(parser.first_nonspace - parser.offset, False)
                parser._add_line()
            parser.current = container

    def _chop_trailing_hashtags(self):
        """chop_trailing_hashtags: drop a trailing run of '#s that is
        preceded by space/tab or starts the line (cmark faithful)."""
        s = self._line
        length = len(s)
        while length > 0 and s[length - 1] in " \t\r\n":
            length -= 1
        if length > 0 and s[length - 1] == "#":
            j = length
            while j > 0 and s[j - 1] == "#":
                j -= 1
            if j == 0 or s[j - 1] in " \t":
                self._chop_n = j
                return
        self._chop_n = None

    # ------------------------------------------------------------ finalization
    def _resolve_refdefs(self):
        buf = "".join(self._buf)
        off = 0
        while off < len(buf) and buf[off] == "[":
            pos = parse_reference_inline(buf, off, self.refs)
            if pos == 0:
                break
            off = pos
        self._buf = [buf[off:]] if off < len(buf) else []
        return not _is_blank("".join(self._buf), 0)

    def _finalize(self, b):
        if b is None:
            return None
        parent = b.parent
        b.open = False
        if b.type in (DOC, CODE_BLOCK, HEADING):
            if b.type == CODE_BLOCK and not b.fenced:
                pass
            if b.type == HEADING and not b.setext:
                pass
            b.end_line = self.line_number
            b.end_column = len(self._line)
            if b.end_column and self._line[b.end_column - 1] == "\n":
                b.end_column -= 1
            if b.end_column and b.end_column > 0 and self._line[b.end_column - 1] == "\r":
                b.end_column -= 1
        else:
            b.end_line = self.line_number - 1
            b.end_column = len(self._line)
            if self._line and self._line[-1] == "\n":
                b.end_column -= 1

        # cmark: only leaf content blocks consume the shared line buffer
        # (parser->content).  Containers (DOC/LIST/ITEM/QUOTE/THEMATIC) leave
        # it alone so the innermost paragraph/code still owns the pending text.
        if b.type == PARA:
            buf = "".join(self._buf)
            self._buf = []
            if not self._resolve_refdefs_in(b, buf):
                self._unlink(b)
                return parent
        elif b.type == CODE_BLOCK:
            buf = "".join(self._buf)
            self._buf = []
            if not b.fenced:
                buf = _remove_trailing_blank_lines(buf) + "\n"
            else:
                pos = 0
                while pos < len(buf) and buf[pos] not in "\r\n":
                    pos += 1
                if pos == 0:
                    b.info = None
                else:
                    import md_inlines as _mi
                    info = _mi._unescape_html(buf[:pos])
                    b.info = _mi._strbuf_unescape(info).strip()
                # skip exactly one line break after the info string (cmark)
                if pos < len(buf) and buf[pos] == "\n":
                    pos += 1
                elif pos + 1 < len(buf) and buf[pos] == "\r" and buf[pos + 1] == "\n":
                    pos += 2
                buf = buf[pos:]
            b.content = buf
        elif b.type in (HEADING, HTML_BLOCK):
            buf = "".join(self._buf)
            self._buf = []
            b.content = buf
        elif b.type == LIST:
            b.tight = True
            item = b.first_child
            while item:
                if item.last_line_blank and item.next:
                    b.tight = False
                    break
                subitem = item.first_child
                while subitem:
                    if (item.next or subitem.next) and _ends_with_blank_line(subitem):
                        b.tight = False
                        break
                    subitem = subitem.next
                if not b.tight:
                    break
                item = item.next
        return parent

    def _resolve_refdefs_in(self, b, buf):
        off = 0
        while off < len(buf) and buf[off] == "[":
            pos = parse_reference_inline(buf, off, self.refs)
            if pos == 0:
                break
            off = pos
        b.content = buf[off:] if off < len(buf) else ""
        return not _is_blank(b.content, 0)

    def _unlink(self, b):
        if b.prev:
            b.prev.next = b.next
        if b.next:
            b.next.prev = b.prev
        if b.parent:
            if b.parent.first_child is b:
                b.parent.first_child = b.next
            if b.parent.last_child is b:
                b.parent.last_child = b.prev
