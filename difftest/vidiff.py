#!/usr/bin/env python3
"""Differential test: ksh vi text objects against vim.

usage: vidiff.py KSH [N [SEED [WORKERS]]]

Each case types TEXT into `ksh -o vi`, moves to a column, runs c/d/y with a
text object, then reads the edit line back: Ctrl-] makes the KEYBD trap in
vidiff-rc.sh write ${.sh.edtext}. Vim runs the same keys in one headless
session. The two resulting lines must match. If vim's operator does nothing,
ksh must leave the line as typed. Failed cases go to vidiff-fails.json, which
reduce.py shrinks.
"""
import json, os, pty, random, select, shutil, signal, struct, fcntl, termios
import subprocess, sys, tempfile, time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

ESC = '\x1b'
TIMEOUT = float(os.environ.get('VIDIFF_TIMEOUT', '8'))

WORDS = ['foo', 'bar_baz', 'x1', 'a', 'ab', '日本', '語', 'é', 'あい', 'アイ', 'mix日本あ']
PUNCT = ['.', '-', ',', ';', '/', '=', '::', '+', '、', '。', '$x', '--no']
OBJS = 'wwWW"\'`()b[]{}B<>'
PAIRS = ['()', '[]', '{}', '<>', '""', "''", '``']


def struct_text(r, depth=0):
    """Nested strings and brackets, like a shell command line."""
    out = []
    for _ in range(r.randint(1, 4)):
        k = r.random()
        if k < .35 or depth > 2:
            out.append(r.choice(WORDS + PUNCT))
        else:
            o, c = r.choice(PAIRS)
            out.append(o + struct_text(r, depth + 1) + c)
        out.append(r.choice(['', ' ', ' ', '  ', ', ', r.choice('\\')]))
    return ''.join(out)


def rand_text(r):
    toks = []
    for _ in range(r.randint(1, 9)):
        k = r.random()
        if k < .35: t = r.choice(WORDS)
        elif k < .55: t = ' ' * r.choice([1, 1, 2, 3])
        elif k < .70: t = r.choice(PUNCT)
        elif k < .80: t = r.choice('"\'`')
        elif k < .93: t = r.choice('()[]{}<>')
        else: t = '\\'
        toks.append(t)
    return ''.join(toks)


def tight_text(r):
    return ''.join(r.choice(' ab"\'()[]{}.-_\\') for _ in range(r.randint(1, 10)))


def gen(n, seed):
    r = random.Random(seed)
    seen, cases = set(), []
    while len(cases) < n:
        k = r.random()
        text = rand_text(r) if k < .4 else struct_text(r) if k < .7 else tight_text(r)
        col = r.randrange(len(text))
        op = r.choice('cdy')
        c1 = r.choice(['', '', '', '2', '3', '4'])
        c2 = '' if c1 else r.choice(['', '', '', '2', '3'])
        ai, obj = r.choice('ai'), r.choice(OBJS)
        main = f'{c1}{op}{c2}{ai}{obj}'
        if op == 'c':
            main += '|' + ESC
        suf = r.choice(['cur', 'reg', 'undo'] + ([] if op == 'y' else ['dot']))
        steps = [main, {'cur': 'iX' + ESC, 'reg': '$p', 'undo': 'u',
                        'dot': r.choice(['0.', '$.', '0.' + '.'])}[suf]]
        key = (text, col, tuple(steps))
        if key in seen:
            continue
        seen.add(key)
        cases.append(dict(text=text, col=col, steps=steps,
                          sig=f'{c1 and "N"}{op}{c2 and "N"}{ai}{obj} {suf}'))
    return cases


VIMSCRIPT = r'''
set nocompatible nomore encoding=utf-8
let cases = json_decode(join(readfile($VIDIFF_CASES), ''))
let out = []
for c in cases
  " type the text like ksh does, so that . can repeat it; u must not undo the typing
  set undolevels=-1
  silent! %delete _
  execute 'normal! i' . c.text . "\<Esc>"
  set undolevels=1000
  call cursor(1, byteidx(c.text, c.col) + 1)
  let @" = ''
  let noop = 0
  for k in c.steps
    try
      execute 'silent! normal! ' . k
    catch
    endtry
    if mode() != 'n' | execute "normal! \<Esc>" | endif
    if k is c.steps[0]
      " an operator that failed or did nothing leaves the cursor where vim stopped: not tested
      let noop = getline(1, '$') == [c.text] && @" == ''
    endif
  endfor
  call add(out, {'line': join(getline(1, '$'), "\n"), 'noop': noop})
endfor
call writefile([json_encode(out)], $VIDIFF_OUT)
qa!
'''


def run_vim(cases, td):
    cf, of, sf = (os.path.join(td, n) for n in ('cases.json', 'vim.out', 'vim.vim'))
    json.dump(cases, open(cf, 'w'))
    open(sf, 'w').write(VIMSCRIPT)
    env = dict(os.environ, VIDIFF_CASES=cf, VIDIFF_OUT=of, TERM='xterm')
    subprocess.run(['vim', '-u', 'NONE', '-i', 'NONE', '-N', '-n', '-es', '-S', sf],
                   env=env, stdin=subprocess.DEVNULL, timeout=600, check=False)
    return json.load(open(of))


def run_ksh(arg):
    ksh, rc, case = arg
    td = tempfile.mkdtemp(prefix='vd')
    out = os.path.join(td, 'out')
    env = dict(PATH=os.environ['PATH'], HOME=td, TERM='xterm', LANG='C.UTF-8',
               PS1='> ', ENV=rc, HISTFILE=os.path.join(td, 'h'),
               VIDIFF_OUT=out, COLUMNS='400', LINES='50')
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(ksh, [ksh, '-o', 'vi', '+o', 'multiline'], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 50, 400, 0, 0))
    keys = case['text'] + ESC + '0'
    if case['col']:
        keys += f"{case['col']}l"
    keys += ''.join(case['steps'])
    if not keys.endswith(ESC):
        keys += ESC
    keys += '\x1d'  # ^] makes the rc trap dump the line
    time.sleep(0.05)
    os.write(fd, keys.encode())
    res = None
    end = time.time() + TIMEOUT
    while time.time() < end:
        if select.select([fd], [], [], 0.05)[0]:
            try:
                os.read(fd, 65536)
            except OSError:
                break
        if os.path.exists(out) and os.path.getsize(out) > 0:
            time.sleep(0.02)
            res = open(out, encoding='utf-8', errors='backslashreplace').read()
            res = res[:-1] if res.endswith('\n') else res
            break
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    os.close(fd)
    shutil.rmtree(td, ignore_errors=True)
    return res


def main():
    ksh = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    workers = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    here = os.path.dirname(os.path.abspath(__file__))
    ed = os.path.join(here, 'vidiff-rc.sh')
    cases = gen(n, seed)
    td = tempfile.mkdtemp(prefix='vdv')
    vim = run_vim(cases, td)
    shutil.rmtree(td, ignore_errors=True)
    assert len(vim) == len(cases), (len(vim), len(cases))
    # ksh runs all the steps, or only the operator if vim's operator did nothing
    runs = [dict(c, steps=c['steps'][:1]) if v['noop'] else c for c, v in zip(cases, vim)]
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(run_ksh, [(ksh, ed, c) for c in runs], chunksize=4))
    want = [c['text'] if v['noop'] else v['line'] for c, v in zip(cases, vim)]
    bad = [(c, v, k) for c, v, k in zip(cases, want, got) if v != k]
    json.dump([c for c, _, _ in bad], open('vidiff-fails.json', 'w'), ensure_ascii=False)
    groups = defaultdict(list)
    for c, v, k in bad:
        groups[c['sig'].split()[0].replace('N', '#')].append((c, v, k))
    for sig in sorted(groups, key=lambda s: -len(groups[s])):
        print(f'--- {sig}: {len(groups[sig])} mismatches')
        for c, v, k in sorted(groups[sig], key=lambda x: len(x[0]['text']))[:3]:
            print(f"  text={c['text']!r} col={c['col']} steps={c['steps']!r}\n"
                  f"    vim={v!r}\n    ksh={k!r}")
    print(f'{len(cases) - len(bad)}/{len(cases)} match')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
