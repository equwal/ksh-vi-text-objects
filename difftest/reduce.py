"""Shrink the failing cases that vidiff.py saved: reduce.py KSH [INDEX]"""
import sys, json, tempfile, os
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
import vidiff
rc = os.path.join(here, 'vidiff-rc.sh')
ksh = sys.argv[1]
fails = json.load(open('vidiff-fails.json'))
if len(sys.argv) > 2:
    fails = [fails[int(sys.argv[2])]]


def bad(t, c, steps):
    if not 0 <= c < len(t):
        return False
    case = dict(text=t, col=c, steps=steps, sig='')
    v = vidiff.run_vim([case], tempfile.mkdtemp())[0]
    run = dict(case, steps=steps[:1]) if v['noop'] else case
    return (t if v['noop'] else v['line']) != vidiff.run_ksh((ksh, rc, run))


for f in fails:
    text, col, steps = f['text'], f['col'], f['steps']
    changed = bad(text, col, steps)
    while changed:
        changed = False
        for size in (16, 8, 4, 2, 1):
            i = 0
            while i < len(text):
                t = text[:i] + text[i + size:]
                c = col - min(size, col - i) if col > i else col
                if bad(t, c, steps):
                    text, col, changed = t, c, True
                else:
                    i += size
    print(json.dumps(dict(text=text, col=col, steps=steps), ensure_ascii=False))
