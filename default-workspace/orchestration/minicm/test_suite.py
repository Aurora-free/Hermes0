"""mini-COMMONMARK test suite (CommonMark 0.31.2 spec conformance).

Run:  python test_suite.py            # spec + adversarial, prints summary
Exit code 0 = all pass.

Components:
  - spec conformance: 655 examples from spec_examples.json (cmark 0.31.2)
  - adversarial: inputs that historically caused infinite loops / quadratic
    blow-up in the reference implementation (fenced code, refdefs, raw HTML)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cm_blocks import BlockParser  # noqa: E402
from cm_render import render  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "spec_examples.json")


def render_md(text):
    p = BlockParser()
    return render(p.run(text), p.refs)


ADVERSARIAL = [
    "*" * 5000,
    "a " * 10000 + "a" * 5000,
    "`" * 9000,
    " " * 5000,
    "[]" * 3000,
    "*_" * 3000,
    "<" * 3000,
    "\n" * 5000,
    "<!-- " * 3000,
    "```" * 3000,
    "[a]: <b> " * 2000,
    ("\"" * 2000 + "a" * 500),
]


def main():
    t0 = time.time()
    examples = json.load(open(SPEC, encoding="utf-8"))
    fails = []
    for i, ex in enumerate(examples):
        got = render_md(ex["markdown"])
        if got != ex["html"]:
            fails.append((i + 1, ex["section"], ex["markdown"], got, ex["html"]))
    total = len(examples)
    print("spec   total=%d pass=%d fail=%d" % (total, total - len(fails), len(fails)))

    ok = True
    for c in ADVERSARIAL:
        try:
            p = BlockParser()
            r = p.run(c)
            render(r, p.refs)
        except Exception as e:  # noqa: BLE001
            ok = False
            print("adversarial FAIL %r: %r" % (c[:40], e))
    print("adversarial:", "ok" if ok else "FAILED")
    print("time=%.3fs" % (time.time() - t0))

    for f in fails[:10]:
        print("=" * 70)
        print("#%d [%s]" % (f[0], f[1]))
        print("MD  :", repr(f[2][:200]))
        print("GOT :", repr(f[3][:300]))
        print("WANT:", repr(f[4][:300]))
    if len(fails) > 10:
        print("... %d more failures" % (len(fails) - 10))
    return 0 if (not fails and ok) else 1


if __name__ == "__main__":
    sys.exit(main())
