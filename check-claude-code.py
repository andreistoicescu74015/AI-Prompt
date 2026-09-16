"""Measure whether the rules in ~/.claude/CLAUDE.md still change Claude Code's
output on the model you actually run.

It runs the same prompt twice per trial, once normally and once with
CLAUDE_CODE_DISABLE_CLAUDE_MDS=1, collects every assistant message including
the ones before tool calls, and counts rule 1 violations in each condition.

    python check-claude-code.py "Write a 500-word launch announcement for a
        caching library. Include three headed sections." -n 3

    python check-claude-code.py "Does the cache here handle expiry? Check."
        --expect "Assumption:" --tools "Read LS Glob Grep"

A rule that scores the same in both conditions is not doing anything: either
the default already covers it, or it never fires. Use --expect to check a rule
that requires a literal string, such as the rule 2 assumption line.

The em dash, en dash, curly quote, emoji, Title Case, bold-colon and horizontal
rule counts are exact. The rest are heuristics and will miss cases; treat a zero
there as "not detected", not as "clean".
"""
import argparse, json, os, re, shutil, subprocess, sys

MARKERS = [
    ('em dash',          re.compile('—')),
    ('en dash',          re.compile('–')),
    ('curly quote',      re.compile('[‘’“”]')),
    ('emoji',            re.compile('[\U0001F000-\U0001FAFF☀-➿]')),
    ('Title Case head',  None),
    ('bold-colon bullet', re.compile(r'^\s*[-*]\s+\*\*[^*]{2,60}\*\*\s*:', re.M)),
    ('not-X-its-Y',      re.compile(r"(isn't|is not|not just|not merely|not only)\b[^.;]{0,70}[;,]?\s*(it's|its|it is|they're|but)\b", re.I)),
    ('slop lexicon',     re.compile(r'\b(delve|leverage|tapestry|testament|showcase\w*|vibrant|seamless\w*|groundbreaking|robust|crucial|pivotal|underscore\w*)\b', re.I)),
    ('-ing tail',        re.compile(r',\s+(ensuring|highlighting|showcasing|underscoring|making it|allowing you to|enabling|empowering)\b', re.I)),
    ('X is the Y of Z',  re.compile(r'\bis the \w+ of\b', re.I)),
    ('serves as',        re.compile(r'\b(serves as|stands as|represents a|boasts)\b', re.I)),
    ('arguing w/ no one', re.compile(r"\b(to be clear|i'm not saying|don't get me wrong|a tempting approach|one might be tempted|you might think|some might say)\b", re.I)),
    ('vague connection', re.compile(r'\b(associated with|tied to|linked to|in connection with)\b', re.I)),
    ('horizontal rule',  re.compile(r'^\s*(-{3,}|\*{3,}|_{3,})\s*$', re.M)),
    ('one-line paragraph', re.compile(r'^(?![#\-*>|\d\s]|Assumption:)(?:\S+\s+){1,11}\S+[.!?]$', re.M)),
]

HEADING = re.compile(r'^#{1,6}\s+(.+?)\s*$', re.M)


def title_case_headings(text, proper):
    n = 0
    for head in HEADING.findall(text):
        words = re.findall(r"[A-Za-z][\w'.-]*", head)
        if len(words) < 2:
            continue
        if sum(1 for w in words[1:]
               if w[0].isupper() and w.lower() not in proper) >= 2:
            n += 1
    return n


def run(prompt, tools, disable_claude_md):
    exe = shutil.which('claude')
    if exe is None:
        sys.exit('claude is not on PATH')
    cmd = [exe, '-p', prompt, '--output-format', 'stream-json', '--verbose']
    if tools:
        cmd += ['--allowedTools'] + tools.split()
    env = dict(os.environ)
    if disable_claude_md:
        env['CLAUDE_CODE_DISABLE_CLAUDE_MDS'] = '1'
    else:
        env.pop('CLAUDE_CODE_DISABLE_CLAUDE_MDS', None)
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-500:] + '\n')
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        content = (rec.get('message') or {}).get('content')
        if rec.get('type') != 'assistant' or not isinstance(content, list):
            continue
        for block in content:
            if block.get('type') == 'text' and block.get('text', '').strip():
                out.append(block['text'].strip())
    return '\n\n'.join(out)


def score(text, proper):
    counts = {}
    for name, rx in MARKERS:
        counts[name] = (title_case_headings(text, proper) if rx is None
                        else len(rx.findall(text)))
    return counts


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('prompt')
    ap.add_argument('-n', type=int, default=3, help='trials per condition')
    ap.add_argument('--expect', help='literal string each run must contain')
    ap.add_argument('--tools', help='space-separated --allowedTools value')
    ap.add_argument('--proper', default='',
                    help='space-separated proper nouns to exempt from the Title Case check')
    args = ap.parse_args()

    proper = set(w.lower() for w in args.proper.split())
    results = {}
    for label, disable in (('CLAUDE.md on', False), ('CLAUDE.md off', True)):
        total = {name: 0 for name, _ in MARKERS}
        words = 0
        expected = 0
        for i in range(args.n):
            sys.stderr.write('%s trial %d/%d\n' % (label, i + 1, args.n))
            text = run(args.prompt, args.tools, disable)
            words += len(text.split())
            if args.expect and args.expect in text:
                expected += 1
            for name, count in score(text, proper).items():
                total[name] += count
        results[label] = (total, words, expected)

    width = max(len(n) for n, _ in MARKERS) + 2
    labels = list(results)
    print()
    print('marker'.ljust(width) + '  '.join(l.rjust(14) for l in labels))
    print('-' * (width + 16 * len(labels)))
    for name, _ in MARKERS:
        print(name.ljust(width) +
              '  '.join(str(results[l][0][name]).rjust(14) for l in labels))
    print('-' * (width + 16 * len(labels)))
    print('words'.ljust(width) +
          '  '.join(str(results[l][1]).rjust(14) for l in labels))
    print()
    for label in labels:
        total, words, expected = results[label]
        hits = sum(total.values())
        line = '%-14s %3d violations / %d runs / %d words = %.2f per 100 words' % (
            label, hits, args.n, words, 100.0 * hits / max(words, 1))
        if args.expect:
            line += '  |  %r in %d/%d' % (args.expect, expected, args.n)
        print(line)


if __name__ == '__main__':
    main()
