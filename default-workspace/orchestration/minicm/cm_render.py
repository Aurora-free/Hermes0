"""Faithful port of cmark 0.31.2 src/html.c — CommonMark HTML rendering.

Renders the block tree produced by cm_blocks.BlockParser. Inlines of
PARAGRAPH/HEADING nodes are produced by the md_inlines port.
"""
import md_inlines
import re as _re

from cm_blocks import (
    DOC, BLOCK_QUOTE, ITEM, LIST, PARA, HEADING, CODE_BLOCK, HTML_BLOCK,
    THEMATIC,
)

# ATX headings: optional closing sequence  =  space/tab then one or more '#',
# only spaces/tabs after.  (cmark blocks.c, ATX heading content trimming.)
_RE_ATX_CLOSE = _re.compile(r"[\t ]+#+[\t ]*$")


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


import string as _string

# cmark HREF_SAFE: characters passed through unescaped in href/src.
# & and ' are in the safe set but need HTML-entity escaping inside href.
_HREF_SAFE = set("-_.+!*'(),%#@?=;:/&$~") | set(_string.ascii_letters) | set(_string.digits)

def _enc_href(t):
    """cmark houdini_escape_href: percent-encode URL for href/src attributes."""
    out = []
    for b in t.encode("utf-8"):
        c = chr(b)
        if b < 128 and c in _HREF_SAFE:
            if c == "&":
                out.append("&amp;")
            elif c == "'":
                out.append("&#39;")
            else:
                out.append(c)
        elif b == 0x20:
            out.append("%20")
        else:
            out.append("%%%02X" % b)
    return "".join(out)


def _cr(out):
    """cmark cr(): append a newline unless the buffer already ends with one."""
    if out and not out[-1].endswith("\n"):
        out.append("\n")


def _process_inlines(node, refs):
    if node.type in (PARA, HEADING):
        content = node.content
        if node.type == HEADING and not node.setext:
            content = _RE_ATX_CLOSE.sub("", content).rstrip(" \t")
        node.inline_html = md_inlines.render_inlines(content, refs)
    c = node.first_child
    while c:
        _process_inlines(c, refs)
        c = c.next


def _is_tight_para(node):
    """True if node is a PARAGRAPH whose grandparent is a tight LIST."""
    p = node.parent
    g = p.parent if p else None
    return p is not None and p.type == ITEM and g is not None and \
        g.type == LIST and g.tight


def _render_node(node, out, refs):
    t = node.type
    if t == DOC:
        c = node.first_child
        while c:
            _render_node(c, out, refs)
            c = c.next
        return
    if t == BLOCK_QUOTE:
        _cr(out)
        out.append("<blockquote>\n")
        c = node.first_child
        while c:
            _render_node(c, out, refs)
            c = c.next
        _cr(out)
        out.append("</blockquote>\n")
    elif t == LIST:
        _cr(out)
        if node.list_type == 1:
            out.append("<ul>\n")
        elif node.start_num == 1:
            out.append("<ol>\n")
        else:
            out.append('<ol start="%d">\n' % node.start_num)
        c = node.first_child
        while c:
            _render_node(c, out, refs)
            c = c.next
        out.append("</ul>\n" if node.list_type == 1 else "</ol>\n")
    elif t == ITEM:
        _cr(out)
        out.append("<li>")
        c = node.first_child
        # cmark html.c: newline after <li> when the item's first child is
        # a block that is not a paragraph (code, heading, quote, ...)
        if c is not None and c.type != PARA:
            out.append("\n")
        while c:
            _render_node(c, out, refs)
            c = c.next
        lc = node.last_child
        if lc is not None and lc.type != PARA:
            _cr(out)  # only add a newline if the buffer doesn't already end with one
        out.append("</li>\n")
    elif t == HEADING:
        _cr(out)
        out.append("<h%d>" % node.level)
        out.append(node.inline_html or "")
        out.append("</h%d>\n" % node.level)
    elif t == CODE_BLOCK:
        if node.fenced and node.info:
            word = node.info.split(" ", 1)[0]
            out.append('<pre><code class="language-%s">' % _esc_html(word))
        else:
            out.append("<pre><code>")
        out.append(_esc_html(node.content))
        out.append("</code></pre>\n")
    elif t == HTML_BLOCK:
        out.append(node.content)
    elif t == THEMATIC:
        _cr(out)
        out.append("<hr />\n")
    elif t == PARA:
        tight = _is_tight_para(node)
        if not tight:
            _cr(out)
            out.append("<p>")
        out.append(node.inline_html or "")
        if not tight:
            out.append("</p>\n")


def render(root, refs):
    """Render a parsed document tree to the CommonMark HTML string."""
    _process_inlines(root, refs)
    out = []
    _render_node(root, out, refs)
    s = "".join(out)
    if s.endswith("\n"):
        s = s[:-1]
    return s
