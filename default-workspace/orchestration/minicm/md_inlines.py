"""Inline parser — faithful port of cmark 0.31.2 inlines.c (reference impl).

Smart punctuation off; no extensions. Unicode classes per spec:
whitespace = Z* + ASCII space/tab/CR/LF/VT/FF; punct-or-symbol = P* or S*.
"""
import re
import unicodedata

from cm_entities import _ENT_RE, _decode_entity

# ---------------------------------------------------------------- classes
_PUNCT_ASCII = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")

def _is_space(ch):
    o = ord(ch)
    if o < 128:
        return ch in " \t\n\r\x0b\x0c"
    return unicodedata.category(ch).startswith("Z")

def _is_punct_sym(ch):
    o = ord(ch)
    if o < 128:
        return ch in _PUNCT_ASCII
    c = unicodedata.category(ch)
    return c.startswith("P") or c.startswith("S")


def normalize_reference(label):
    """Unicode full case-fold + ASCII-whitespace normalize (cmark faithful).
    ẞ and ß both fold to 'ss', so [ẞ] matches [SS]. None if empty."""
    if not label:
        return None
    s = label.casefold()
    s = s.strip(_WS_CHARS)
    s = re.sub("[" + _WS_CHARS + "]+", " ", s)
    return s if s else None


_WS_CHARS = "".join(chr(c) for c in (32, 9, 10, 13, 11, 12))


# ---------------------------------------------------------------- url/title
_HREF_SAFE = frozenset(
    b"!#$%()*+,-./0123456789:;=?@ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz~"
)

def _enc_href(s):
    out = []
    for b in s.encode("utf-8"):
        if b in _HREF_SAFE:
            out.append(chr(b))
        elif b == 38:
            out.append("&amp;")
        elif b == 39:
            out.append("&#x27;")
        else:
            out.append("%%%02X" % b)
    return "".join(out)

def _unescape_html(s):
    """Decode entities (known named + numeric); unknown left literal."""
    i = 0
    out = []
    n = len(s)
    while i < n:
        if s[i] == "&":
            m = _ENT_RE.match(s, i)
            if m:
                ch = _decode_entity(m)
                if ch != m.group(0):
                    out.append(ch)
                    i = m.end()
                    continue
        out.append(s[i])
        i += 1
    return "".join(out)

def _strbuf_unescape(s):
    out = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "\\" and i + 1 < n and s[i + 1] in _PUNCT_ASCII:
            out.append(s[i + 1])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)

def _clean_url(chunk):
    s = chunk.strip(" \t\n\r")
    s = _unescape_html(s)
    return _strbuf_unescape(s)

def _clean_title(chunk):
    if chunk == "":
        return None
    s = chunk
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1]
    elif len(s) >= 2 and s[0] == "(" and s[-1] == ")":
        s = s[1:-1]
    s = _unescape_html(s)
    s = _strbuf_unescape(s)
    return s

# ---------------------------------------------------------------- scanners
# Note: these are used with .match(s, p) where p can be > 0, so they must NOT
# carry a '^' anchor (re.match already anchors at p). cmark's re2c scanners are
# cursor-anchored, matching this.
_RE_AUTOLINK_URI = re.compile(r"[A-Za-z][A-Za-z0-9.+-]{1,31}:[^\x00-\x20<>]*>")
_RE_AUTOLINK_EMAIL = re.compile(
    r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"([.][a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*>"
)
_ATTR = (
    r"[ \t\r\n\f\v]+[a-zA-Z_:][a-zA-Z0-9:._-]*"
    r"(?:(?:[ \t\r\n\f\v]*=[ \t\r\n\f\v]*"
    r"(?:[^ \t\r\n\v\f\"'=<>`]+|'[^'\x00]*'|\x22[^\x22\x00]*\x22))?)"
)
_RE_OPEN_TAG = re.compile(r"[A-Za-z][A-Za-z0-9-]*(?:" + _ATTR + r")*[ \t\r\n\f\v]*/?>")
_RE_CLOSE_TAG = re.compile(r"/[A-Za-z][A-Za-z0-9-]*[ \t\r\n\f\v]*>")
_RE_COMMENT = re.compile(r"--(?:[^\x00>-]+|-[^-\x00>]|--[^-\x00>])*-->")
_RE_CDATA = re.compile(r"CDATA\[(?:[^\]\x00]+|\][^\]\x00]|]][^>\x00])*")
_RE_DECL = re.compile(r"[A-Za-z]+[^>\x00]*")
_RE_PI = re.compile(r"(?:[^?>\x00]+|\?[^>\x00]|>)+")

_RE_TITLE = re.compile(
    r"\"(?:\\[!-~]|[^\"\x00])*\""
    r"|'(?:\\[!-~]|[^'\x00])*'"
    r"|\((?:\\[!-~]|[^()\x00])*\)"
)
MAX_LINK_LABEL_LENGTH = 1000

def _scan_spacechars(s, p):
    n = len(s)
    i = p
    while i < n and s[i] in " \t\n\r\x0b\x0c":
        i += 1
    return i - p

def _scan_link_title(s, p):
    m = _RE_TITLE.match(s, p)
    return m.end() - p if m else 0

def _scan_link_url(s, p):
    """cmark manual_scan_link_url. Returns interior string or None."""
    n = len(s)
    if p < n and s[p] == "<":
        i = p + 1
        while i < n:
            c = s[i]
            if c == ">":
                i += 1
                break
            elif c == "\\":
                i += 2
            elif c in "\n<":
                return None
            else:
                i += 1
        if i >= n:
            return None
        return s[p + 1:i - 1]
    i = p
    nb = 0
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n and s[i + 1] in _PUNCT_ASCII:
            i += 2
        elif c == "(":
            nb += 1
            i += 1
            if nb > 32:
                return None
        elif c == ")":
            if nb == 0:
                break
            nb -= 1
            i += 1
        elif c in _WS_CHARS:
            if i == p:
                return None
            break
        else:
            i += 1
    if i >= n or nb != 0:
        return None
    return s[p:i]

def _link_label(s, p):
    """cmark link_label from a `[` at p. Returns (label, pos_after_]) or None."""
    n = len(s)
    start = p + 1
    length = 0
    i = p + 1
    while i < n:
        c = s[i]
        if c in "[]":
            break
        if c == "\\":
            i += 1
            length += 1
            if i < n and s[i] in _PUNCT_ASCII:
                i += 1
                length += 1
        else:
            i += 1
            length += 1
        if length > MAX_LINK_LABEL_LENGTH:
            return None
    if i < n and s[i] == "]":
        raw = s[start:i].strip(" \t\n\r")
        return raw, i + 1
    return None

# ---------------------------------------------------------------- nodes
class I:
    __slots__ = ("kind", "text", "url", "title", "children")

    def __init__(self, kind, text=None, url=None, title=None):
        self.kind = kind
        self.text = text
        self.url = url
        self.title = title
        self.children = []

class Delim:
    __slots__ = ("ch", "can_open", "can_close", "length", "position",
                 "textref", "prev", "next")

    def __init__(self, ch, can_open, can_close, length, position, textref):
        self.ch = ch
        self.can_open = can_open
        self.can_close = can_close
        self.length = length
        self.position = position
        self.textref = textref
        self.prev = None
        self.next = None

class Bracket:
    __slots__ = ("image", "out_idx", "lab_start", "bracket_after", "prev")

    def __init__(self, image, out_idx, lab_start):
        self.image = image
        self.out_idx = out_idx
        self.lab_start = lab_start
        self.bracket_after = False
        self.prev = None

# ---------------------------------------------------------------- delimiter logic
def _scan_delims(subj, c):
    s, n = subj.s, subj.n
    p = subj.pos
    before = "\n" if p == 0 else s[p - 1]
    j = p
    if c in "'\"":
        run = 1
        j = p + 1
    else:
        run = 0
        while j < n and s[j] == c:
            run += 1
            j += 1
    after = s[j] if j < n else "\n"
    b_ps = _is_punct_sym(before)
    a_ps = _is_punct_sym(after)
    b_sp = _is_space(before)
    a_sp = _is_space(after)
    left = run > 0 and not a_sp and (not a_ps or b_sp or b_ps)
    right = run > 0 and not b_sp and (not b_ps or a_sp or a_ps)
    if c == "_":
        open_ = left and (not right or b_ps)
        close = right and (not left or a_ps)
    else:
        open_ = left
        close = right
    subj.pos = j
    return run, open_, close

def _remove_delimiter(subj, d):
    if d is None:
        return
    if d.next is not None:
        d.next.prev = d.prev
    if d.prev is not None:
        d.prev.next = d.next
    else:
        subj._first_delim = d.next
    if subj.last_delim is d:
        subj.last_delim = d.prev

def _insert_emph(subj, opener, closer):
    onode, cnode = opener.textref, closer.textref
    use = 2 if (len(cnode.text) >= 2 and len(onode.text) >= 2) else 1
    out = subj.out
    onode.text = onode.text[: len(onode.text) - use]
    cnode.text = cnode.text[use:]
    d = closer.prev
    while d is not None and d is not opener:
        tmp = d.prev
        _remove_delimiter(subj, d)
        d = tmp
    wrap = I("emph" if use == 1 else "strong")
    io = out.index(onode)
    ic = out.index(cnode)
    wrap.children = out[io + 1: ic]
    del out[io + 1: ic]
    out.insert(io + 1, wrap)
    if onode.text == "":
        out.remove(onode)
        _remove_delimiter(subj, opener)
    if cnode.text == "":
        if cnode in out:
            out.remove(cnode)
        nxt = closer.next
        _remove_delimiter(subj, closer)
        return nxt
    return closer

def _process_emphasis(subj, stack_bottom):
    cand = subj.last_delim
    closer = None
    while cand is not None and cand.position >= stack_bottom:
        closer = cand
        cand = cand.prev
    openers_bottom = [stack_bottom] * 15
    while closer is not None:
        if not closer.can_close:
            closer = closer.next
            continue
        ch = closer.ch
        if ch == "_":
            ob = 2 + (3 if closer.can_open else 0) + (closer.length % 3)
        elif ch == "*":
            ob = 8 + (3 if closer.can_open else 0) + (closer.length % 3)
        else:
            closer = closer.next
            continue
        o = closer.prev
        found = False
        while o is not None and o.position >= openers_bottom[ob]:
            if o.can_open and o.ch == ch:
                if not (closer.can_open or o.can_close) or \
                        closer.length % 3 == 0 or \
                        (o.length + closer.length) % 3 != 0:
                    found = True
                    break
            o = o.prev
        if found:
            closer = _insert_emph(subj, o, closer)
        else:
            openers_bottom[ob] = closer.position
            if not closer.can_open:
                _remove_delimiter(subj, closer)
            closer = closer.next
    while subj.last_delim is not None and subj.last_delim.position >= stack_bottom:
        _remove_delimiter(subj, subj.last_delim)


# ---------------------------------------------------------------- subject
class Subject:
    def __init__(self, s, refs):
        s = s.rstrip(" \t\n\r")
        self.s = s
        self.n = len(s)
        self.pos = 0
        self.out = []
        self.last_delim = None
        self.last_bracket = None
        self.refs = refs
        self.no_link_openers = False
        self._skip_flags = set()
        self._first_delim = None

    def push_delim(self, ch, can_open, can_close, length, node):
        d = Delim(ch, can_open, can_close, length, self.pos, node)
        d.prev = self.last_delim
        if self.last_delim is not None:
            self.last_delim.next = d
        else:
            self._first_delim = d
        self.last_delim = d

    def push_bracket(self, image, out_idx, lab_start):
        b = Bracket(image, out_idx, lab_start)
        b.prev = self.last_bracket
        if self.last_bracket is not None:
            self.last_bracket.bracket_after = True
        self.last_bracket = b
        if not image:
            self.no_link_openers = False

    def pop_bracket(self):
        b = self.last_bracket
        if b is not None:
            self.last_bracket = b.prev

# ---------------------------------------------------------------- handlers
def _handle_backslash(subj):
    s, n = subj.s, subj.n
    p = subj.pos + 1
    if p < n and s[p] in _PUNCT_ASCII:
        subj.pos = p + 1
        subj.out.append(I("text", s[p]))
    elif p < n and s[p] == "\n":
        subj.pos = p + 1
        subj.out.append(I("linebreak"))
    elif p + 1 < n and s[p] == "\r" and s[p + 1] == "\n":
        subj.pos = p + 2
        subj.out.append(I("linebreak"))
    elif p < n and s[p] == "\r":
        subj.pos = p + 1
        subj.out.append(I("linebreak"))
    else:
        subj.pos = p
        subj.out.append(I("text", "\\"))

def _handle_newline(subj):
    s, n = subj.s, subj.n
    p = subj.pos
    nlpos = p
    if s[p] == "\r":
        p += 1
    if p < n and s[p] == "\n":
        p += 1
    while p < n and s[p] in " \t":
        p += 1
    hard = nlpos > 1 and s[nlpos - 1] == " " and s[nlpos - 2] == " "
    subj.pos = p
    subj.out.append(I("linebreak" if hard else "softbreak"))

def _norm_code(t):
    out = []
    has_nonspace = False
    i = 0
    n = len(t)
    while i < n:
        c = t[i]
        if c == "\r":
            if i + 1 < n and t[i + 1] != "\n":
                out.append(" ")
        elif c == "\n":
            out.append(" ")
        else:
            out.append(c)
        if c != " ":
            has_nonspace = True
        i += 1
    s2 = "".join(out)
    if has_nonspace and len(s2) >= 2 and s2.startswith(" ") and s2.endswith(" "):
        s2 = s2[1:-1]
    return s2

def _handle_backticks(subj):
    s, n = subj.s, subj.n
    p = subj.pos
    initpos = p
    while p < n and s[p] == "`":
        p += 1
    openlen = p - initpos
    j = p
    while j < n:
        while j < n and s[j] != "`":
            j += 1
        if j >= n:
            break
        k = j
        while k < n and s[k] == "`":
            k += 1
        if k - j == openlen:
            subj.pos = k
            subj.out.append(I("code", _norm_code(s[p:j])))
            return
        j = k
    subj.pos = initpos + openlen
    subj.out.append(I("text", s[initpos: initpos + openlen]))

def _handle_entity(subj):
    s, n = subj.s, subj.n
    p = subj.pos
    m = _ENT_RE.match(s, p)
    if m:
        ch = _decode_entity(m)
        if ch != m.group(0):
            subj.pos = m.end()
            subj.out.append(I("text", ch))
            return
    subj.pos = p + 1
    subj.out.append(I("text", "&"))

def _handle_delim(subj):
    s = subj.s
    p = subj.pos
    c = s[p]
    run, open_, close = _scan_delims(subj, c)
    node = I("text", c * run)
    subj.out.append(node)
    if open_ or close:
        subj.push_delim(c, open_, close, run, node)

def _handle_pointy_brace(subj):
    s, n = subj.s, subj.n
    p = subj.pos + 1  # past <
    m = _RE_AUTOLINK_URI.match(s, p)
    if m:
        url = s[p:m.end() - 1]
        subj.pos = m.end()
        node = I("link", url=url, title=None)
        node.children.append(I("text", url))
        subj.out.append(node)
        return
    m = _RE_AUTOLINK_EMAIL.match(s, p)
    if m:
        addr = s[p:m.end() - 1]
        subj.pos = m.end()
        node = I("link", url="mailto:" + addr, title=None)
        node.children.append(I("text", addr))
        subj.out.append(node)
        return
    c = s[p] if p < n else ""
    matchlen = 0
    if c == "!":
        if p + 2 < n and s[p + 1] == "-" and s[p + 2] == "-":
            if s[p + 3] == ">":
                matchlen = 4
            elif s[p + 3] == "-" and s[p + 4] == ">":
                matchlen = 5
            else:
                m = _RE_COMMENT.match(s, p + 1)
                if m:
                    matchlen = m.end() - (p + 1) + 1
        elif c == "!":
            if p + 1 < n and s[p + 1] == "[":
                m = _RE_CDATA.match(s, p + 2)
                if m:
                    matchlen = (m.end() - (p + 2)) + 5
                    if p + matchlen > n:
                        matchlen = 0
            elif p + 1 < n:
                m = _RE_DECL.match(s, p + 1)
                if m:
                    matchlen = (m.end() - (p + 1)) + 2
                    if p + matchlen > n:
                        matchlen = 0
    elif c == "?":
        m = _RE_PI.match(s, p + 1)
        matchlen = (m.end() - (p + 1)) + 3 if m else 0
        if p + matchlen > n:
            matchlen = 0
    else:
        m = _RE_OPEN_TAG.match(s, p) or _RE_CLOSE_TAG.match(s, p)
        matchlen = m.end() - p if m else 0
    if matchlen > 0:
        subj.pos = p + matchlen
        # cmark: src = input + (pos_before_match - 1)  => include the '<'
        subj.out.append(I("html", s[p - 1: subj.pos]))
        return
    subj.pos = p
    subj.out.append(I("text", "<"))

def _handle_open_bracket(subj):
    s, n = subj.s, subj.n
    p = subj.pos + 1  # past [
    node = I("text", "[")
    subj.out.append(node)
    subj.push_bracket(False, len(subj.out) - 1, p)
    subj.pos = p

def _handle_bang(subj):
    s, n = subj.s, subj.n
    p = subj.pos + 1  # past !
    if p < n and s[p] == "[":
        q = p + 1
        node = I("text", "![")
        subj.out.append(node)
        subj.push_bracket(True, len(subj.out) - 1, q)
        subj.pos = q
    else:
        subj.out.append(I("text", "!"))
        subj.pos = p

def _handle_close_bracket(subj):
    s, n = subj.s, subj.n
    p = subj.pos + 1  # past ]
    initial_pos = p
    opener = subj.last_bracket
    if opener is None:
        subj.pos = p
        subj.out.append(I("text", "]"))
        return
    is_image = opener.image
    if not is_image and subj.no_link_openers:
        subj.pop_bracket()
        subj.pos = p
        subj.out.append(I("text", "]"))
        return
    after_link_text_pos = p
    url = title = None
    matched = False
    # --- inline link ---
    if p < n and s[p] == "(":
        sps = _scan_spacechars(s, p + 1)
        url_start = p + 1 + sps
        interior = _scan_link_url(s, url_start)
        if interior is not None:
            # compute end of url: replay length
            if s[url_start] == "<":
                i = url_start + 1
                while i < n:
                    c = s[i]
                    if c == ">":
                        i += 1
                        break
                    elif c == "\\":
                        i += 2
                    else:
                        i += 1
                endurl = i
            else:
                i = url_start
                nb = 0
                while i < n:
                    c = s[i]
                    if c == "\\" and i + 1 < n and s[i + 1] in _PUNCT_ASCII:
                        i += 2
                    elif c == "(":
                        nb += 1
                        i += 1
                    elif c == ")":
                        if nb == 0:
                            break
                        nb -= 1
                        i += 1
                    elif c in _WS_CHARS:
                        break
                    else:
                        i += 1
                endurl = i
            starttitle = endurl + _scan_spacechars(s, endurl)
            endtitle = starttitle + _scan_link_title(s, starttitle)
            endall = endtitle + _scan_spacechars(s, endtitle)
            if endall < n and s[endall] == ")":
                subj.pos = endall + 1
                url = _clean_url(interior)
                title = _clean_title(s[starttitle:endtitle])
                matched = True
    if not matched:
        subj.pos = after_link_text_pos
        found = None
        rr = _link_label(s, p) if p < n and s[p] == "[" else None
        if rr is not None:
            found = rr
            subj.pos = rr[1]
        raw = found[0] if found else ""
        if found is None:
            subj.pos = initial_pos
        if (found is None or raw == "") and not opener.bracket_after:
            raw = s[opener.lab_start: initial_pos - 1].strip(_WS_CHARS)
            found = (raw, subj.pos)
        ref = subj.refs.get(normalize_reference(raw))
        if ref is not None:
            url, title = ref
            subj.pos = found[1]
            matched = True
    if not matched:
        subj.pop_bracket()
        subj.pos = initial_pos
        subj.out.append(I("text", "]"))
        return
    node = I("image" if is_image else "link", url=url, title=title)
    _process_emphasis(subj, opener.lab_start)
    node.children = subj.out[opener.out_idx + 1:]
    del subj.out[opener.out_idx:]
    subj.out.append(node)
    subj.pop_bracket()
    if not is_image:
        subj.no_link_openers = True

# ---------------------------------------------------------------- driver
_SPECIAL = frozenset("\r\n\\`&_*[]<!")

def _find_special(s, p):
    n = len(s)
    while p < n and s[p] not in _SPECIAL:
        p += 1
    return p

def _parse_inline(subj):
    s, n = subj.s, subj.n
    p = subj.pos
    if p >= n:
        return False
    c = s[p]
    if c in "\r\n":
        _handle_newline(subj)
    elif c == "`":
        _handle_backticks(subj)
    elif c == "\\":
        _handle_backslash(subj)
    elif c == "&":
        _handle_entity(subj)
    elif c == "<":
        _handle_pointy_brace(subj)
    elif c in "*_":
        _handle_delim(subj)
    elif c in "-.":
        subj.pos = p + 1
        subj.out.append(I("text", c))
    elif c in "\'\"":
        subj.pos = p + 1
        subj.out.append(I("text", c))
    elif c == "[":
        _handle_open_bracket(subj)
    elif c == "]":
        _handle_close_bracket(subj)
    elif c == "!":
        _handle_bang(subj)
    else:
        e = _find_special(s, p)
        chunk = s[p:e]
        if e < n and s[e] in "\r\n":
            chunk = chunk.rstrip(" \t")
        subj.pos = e
        if chunk:
            subj.out.append(I("text", chunk))
    return True

# ---------------------------------------------------------------- rendering
def _esc_html(t):
    out = []
    for ch in t:
        if ch == "&":
            out.append("&amp;")
        elif ch == "<":
            out.append("&lt;")
        elif ch == ">":
            out.append("&gt;")
        elif ch == '"':
            out.append("&quot;")
        else:
            out.append(ch)
    return "".join(out)

def _flat_text(node):
    out = []
    for c in node.children:
        if c.kind == "text":
            out.append(c.text)
        else:
            out.append(_flat_text(c))
    return "".join(out)

def _render_i(node):
    k = node.kind
    if k == "text":
        return _esc_html(node.text)
    if k == "code":
        return "<code>%s</code>" % _esc_html(node.text)
    if k == "html":
        return node.text
    if k == "softbreak":
        return "\n"
    if k == "linebreak":
        return "<br />\n"
    if k in ("emph", "strong"):
        inner = "".join(_render_i(c) for c in node.children)
        tag = "em" if k == "emph" else "strong"
        return "<%s>%s</%s>" % (tag, inner, tag)
    if k == "link":
        inner = "".join(_render_i(c) for c in node.children)
        a = ' href="%s"' % _enc_href(node.url or "")
        if node.title:
            a += ' title="%s"' % _esc_html(node.title)
        return "<a%s>%s</a>" % (a, inner)
    if k == "image":
        alt = _flat_text(node)
        a = ' src="%s" alt="%s"' % (_enc_href(node.url or ""), _esc_html(alt))
        if node.title:
            a += ' title="%s"' % _esc_html(node.title)
        return "<img%s />" % a
    return ""

def render_inlines(text, refs):
    """Parse inline content of a paragraph/heading -> HTML string (cmark rules)."""
    subj = Subject(text, refs)
    while _parse_inline(subj):
        pass
    _process_emphasis(subj, 0)
    return "".join(_render_i(nd) for nd in subj.out)
