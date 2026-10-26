# mini-COMMONMARK

Faithful Python port of the cmark 0.31.2 block + inline parser and HTML
renderer, built as the reference implementation for the deepseek-harness
orchestrator's markdown test corpus.

Conformance: **655/655** CommonMark 0.31.2 spec examples pass
(`test_suite.py`), plus adversarial loop-safety inputs.

## Files

| file | role |
|---|---|
| `cm_blocks.py` | block parser (port of cmark `src/blocks.c`): containers (document, list, item, blockquote), leaf blocks (paragraph, code, heading, thematic break, HTML block 1-7), reference-definition consumption |
| `md_inlines.py` | inline parser (port of cmark `src/inlines.c`): emphasis delimiter stacks, code spans, autolinks, raw HTML, links/images (incl. unicode casefold reference lookup), backslash escapes, entities, hard breaks |
| `cm_render.py` | HTML renderer (port of cmark `src/render.c` semantics) |
| `cm_entities.py` | HTML5 entity decoding (cmark `houdini_unescape_html` semantics via C-level `html.unescape`) |
| `test_suite.py` | test runner: 655-example spec conformance + adversarial loop-safety checks |
| `spec_examples.json` | CommonMark 0.31.2 spec examples (655), indexed 0-based; example N = index N-1 |

## Usage

```python
import cm_blocks, cm_render

def render_markdown(text: str) -> str:
    parser = cm_blocks.BlockParser()
    root = parser.run(text)
    return cm_render.render(root, parser.refs)
```

## Test

```
python test_suite.py
# spec   total=655 pass=655 fail=0
# adversarial: ok
```

## Notable porting decisions

- Entity decoding: delegates to Python's `html.unescape` (HTML5 named
  entities + numeric refs, invalid code points to U+FFFD) — identical to
  cmark's `houdini_unescape_html` semantics.
- Unicode reference keys use `unicodedata` casefold, matching cmark's
  utf8proc-based normalization for the spec corpus.
- Reference definitions are collected document-wide (anywhere in the tree,
  including inside blockquotes); links resolve against the full map at
  render time, so definitions may appear after their use.
- Fenced code blocks: the opening-fence line contributes its tail (info
  string) to the content; exactly one line break after the info string is
  stripped (cmark `S_finalize` fenced behavior), preserving leading blank
  content lines.
