"""HTML entity handling (port of cmark utils.c: houdini_unescape_html semantics).

Uses Python's html.unescape (C-level HTML5 entity parser), which implements
exactly the HTML5 named-entities + numeric-reference semantics cmark relies on.
"""
import re
import html as _htmlmod

_ESCAPABLE = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")  # 21+punct
# CommonMark entity form: '&' name ';' (semicolon required, cmark scanner).
_ENT_RE = re.compile(r"&([A-Za-z][A-Za-z0-9]{0,30}|#[Xx][0-9a-fA-F]{1,6}|#[0-9]{1,7});")

def _decode_entity(m):
    return _htmlmod.unescape(m.group(0))

