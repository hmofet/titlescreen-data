#!/usr/bin/env python3
"""
Title Screen corrections: checks and reviews the correction issues on this repository.

    review.py check <issue>        an issue was opened or edited: check every field against the live data,
                                   close it with the reasons when one fails, reopen it once fixed
    review.py review [--dry-run]   every correction: roll duplicates up into the oldest report, close the ones
                                   a build has since applied, score the rest and refresh the digest issue

The rules are written out for contributors at https://titlescreen.org/corrections.
A score ranks corrections for a person to check; it never applies one. Accepting a correction is a person
adding the `accepted` label; the digest then carries its override, ready for the build.

A review ends by telling Herald (the maintainer's notification hub): a quiet heartbeat every run, which a
dead-man timer there watches (GitHub turns scheduled workflows off after 60 days without a commit), and a
notification only when something waits on a person.

Standard library only. Environment: GITHUB_TOKEN, GITHUB_REPOSITORY (owner/name), TS_API
(default https://titlescreen.org/api/v1), and optionally HERALD_TOKEN and HERALD_URL.
"""
import datetime as dt
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

FACTS = ('date', 'publisher', 'developer', 'genre', 'players')
API_FIELD = {'date': 'date', 'publisher': 'publisher', 'developer': 'developer', 'genre': 'genre', 'players': 'players_max'}
OVERRIDE_FIELD = {'date': 'date', 'publisher': 'publisher', 'developer': 'developer', 'genre': 'genre', 'players': 'players_max'}

# the issue form's dropdown, strongest first: (key, the option as the form shows it, weight, short name for a reason line)
EVIDENCE = [
    ('media', 'The game itself: cartridge, disc, box, manual or title screen (photo or scan)', 3.0, 'the game itself'),
    ('maker', "The publisher's or developer's own record: site, press release, catalogue", 2.5, "the publisher's own record"),
    ('registry', 'A ratings board or registry: ESRB, PEGI, CERO, USK, ACB, a copyright office', 2.5, 'a ratings board or registry'),
    ('press', 'A magazine or newspaper of the time (scan, with the page)', 2.0, 'the press of the time'),
    ('book', 'A reference book (give the page)', 1.5, 'a reference book'),
    ('database', 'Another database or wiki', 0.5, 'another database'),
]
KIND = {label: (key, w, short) for key, label, w, short in EVIDENCE}
KIND_BY_KEY = {key: (w, short) for key, label, w, short in EVIDENCE}

# the form's headings -> field ids
HEADINGS = {'Release': 'release', 'Fact': 'fact', 'Published now': 'current', 'Proposed value': 'proposed',
            'Kind of evidence': 'evidence_kind', 'Link to the evidence': 'evidence_url',
            'What the evidence shows': 'evidence_detail', 'Checks': 'confirm'}

LIKELY, PLAUSIBLE = 4.0, 2.5
GENRE_CAP = 1.5
BANDS = {'likely-accurate': 'Likely accurate', 'plausible': 'Plausible', 'weak-evidence': 'Weak evidence'}
LABELS = {
    'correction': ('1f5fa8', 'A correction to a fact'),
    'needs-review': ('fbca04', 'Passed the checks; not yet scored'),
    'likely-accurate': ('2f7a45', 'Scored: the evidence and the sources point the same way'),
    'plausible': ('c9852f', 'Scored: some support, a person should look'),
    'weak-evidence': ('cfd3d7', 'Scored: little to go on yet'),
    'conflicting': ('a8560f', 'Other reports propose a different value for the same fact'),
    'new-company': ('d4c5f9', 'Names a company Title Screen does not list yet'),
    'accepted': ('0e8a16', 'Checked by a person; the next build applies it'),
    'rejected': ('b60205', 'Checked by a person and not applied'),
    'invalid': ('e4e669', 'A field did not check out'),
    'duplicate': ('cfd3d7', 'Rolled up into an earlier report'),
    'digest': ('5319e7', 'The corrections digest the bot keeps'),
}
NEW_ACCOUNT_DAYS = 30
MARK_CHECK = '<!-- ts-bot:check -->'
MARK_ROLLUP = '<!-- ts-bot:rollup -->'
MARK_DIGEST = '<!-- ts-bot:digest -->'
COMPANY = re.compile(r'^(?P<name>.+?)\s*\[(?P<id>c[a-z0-9]{7})\]$')
NEW_COMPANY = re.compile(r'^new company:\s*(?P<name>\S.{0,79})$', re.I)
GENRE = re.compile(r'\[(?P<id>[a-z0-9][a-z0-9/-]*)\]$')
DATE = re.compile(r'^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$')
URL = re.compile(r'^https?://[^\s/]+\.[^\s]+$')
OURS = re.compile(r'^https?://([a-z0-9-]+\.)*titlescreen\.(org|ca)([/:?#]|$)', re.I)


# ---------- the live data ----------

class Data:
    """The public API, read once per id per run, paced under its rate limit."""

    def __init__(self, base: str = None, pause: float = 1.05) -> None:
        self.base = (base or os.environ.get('TS_API') or 'https://titlescreen.org/api/v1').rstrip('/')
        self.pause = pause
        self.cache: Dict[str, object] = {}
        self.last = 0.0

    def get(self, path: str):
        if path in self.cache:
            return self.cache[path]
        wait = self.last + self.pause - time.time()
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(f'{self.base}/{path}', headers={'user-agent': 'titlescreen-corrections-bot'})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    out = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    out = None
                    break
                if e.code in (429, 503) and attempt < 2:
                    time.sleep(60)
                    continue
                raise
        self.last = time.time()
        self.cache[path] = out
        return out

    def release(self, rid: str) -> Optional[dict]:
        return self.get(f'releases/{rid}') if re.fullmatch(r'r[a-z0-9]{7}', rid or '') else None

    def evidence(self, rid: str) -> List[dict]:
        return self.get(f'releases/{rid}/evidence') or []

    def company(self, cid: str) -> Optional[dict]:
        return self.get(f'companies/{cid}')

    def genres(self) -> Dict[str, dict]:
        return {g['id']: g for g in (self.get('genres') or [])}

    def version(self) -> str:
        return (self.get('meta') or {}).get('version', '')


def published(rel: dict, fact: str) -> str:
    """The live value of a fact, in the form a proposal is normalised to."""
    if fact == 'date':
        y = rel.get('year')
        if not y:
            return ''
        parts = [str(y)]
        if rel.get('month'):
            parts.append(f"{int(rel['month']):02d}")
            if rel.get('day'):
                parts.append(f"{int(rel['day']):02d}")
        return '-'.join(parts)
    if fact in ('publisher', 'developer'):
        return rel.get(f'{fact}_id', '')
    if fact == 'genre':
        return rel.get('genre_id', '')
    if fact == 'players':
        return str(rel['players_max']) if rel.get('players_max') else ''
    return ''


def shown(fact: str, value: str, data: 'Data') -> str:
    if not value:
        return ''
    if fact in ('publisher', 'developer'):
        c = data.company(value)
        return f"{c['name']} [{value}]" if c else value
    if fact == 'genre':
        g = data.genres().get(value)
        return f"{g['name']} [{value}]" if g else value
    return value


def fold(name: str) -> str:
    k = ''.join(c for c in unicodedata.normalize('NFKD', name) if not unicodedata.combining(c)).lower()
    return re.sub(r'[^a-z0-9]+', '', k.replace('&', 'and'))


# ---------- one issue ----------

@dataclass
class Report:
    number: int
    author: str
    created: str
    state: str
    labels: set
    fields: Dict[str, str]
    release: Optional[dict] = None
    fact: str = ''
    value: str = ''              # normalised: a date, a company id, 'new:<fold>', a genre id, a player count
    display: str = ''            # the value as people read it
    current: str = ''            # the live value, normalised the same way
    current_display: str = ''
    kind: str = ''
    weight: float = 0.0
    url: str = ''
    detail: str = ''
    problems: List[str] = field(default_factory=list)

    @property
    def new_company(self) -> bool:
        return self.value.startswith('new:')


def parse_body(body: str) -> Dict[str, str]:
    out = {}
    parts = re.split(r'^###\s+(.+?)\s*$', (body or '').replace('\r\n', '\n'), flags=re.M)
    for head, text in zip(parts[1::2], parts[2::2]):
        k = HEADINGS.get(head.strip())
        if k:
            text = text.strip()
            out[k] = '' if text == '_No response_' else text
    return out


def is_correction(issue: dict) -> bool:
    names = {l['name'] for l in issue.get('labels', [])}
    return 'correction' in names or ('### Release' in (issue.get('body') or '') and '### Proposed value' in (issue.get('body') or ''))


def read(issue: dict, data: Data) -> Report:
    """An issue as a report, with every problem that makes it unusable."""
    f = parse_body(issue.get('body') or '')
    r = Report(number=issue['number'], author=(issue.get('user') or {}).get('login', ''), created=issue.get('created_at', ''),
               state=issue.get('state', 'open'), labels={l['name'] for l in issue.get('labels', [])}, fields=f)
    p = r.problems
    rid = (f.get('release') or '').strip().strip('`').lower()
    r.release = data.release(rid)
    if not r.release:
        p.append(f'**Release**: `{rid or "(empty)"}` is not a release id on titlescreen.org (it looks like `rve3nkew`).')
    r.fact = (f.get('fact') or '').strip().strip('`').lower()
    if r.fact not in FACTS:
        p.append(f'**Fact**: `{r.fact or "(empty)"}` cannot be corrected here; the facts are {", ".join(f"`{x}`" for x in FACTS)}.')
    raw = (f.get('proposed') or '').strip().strip('`')
    if r.fact in FACTS:
        r.value, r.display, err = normalise(r.fact, raw, data)
        if err:
            p.append(f'**Proposed value**: {err}')
    if r.release and r.fact in FACTS:
        r.current = published(r.release, r.fact)
        r.current_display = shown(r.fact, r.current, data)
        # proposing the published value is an error at the check, and in a review means a build applied it
        if r.fact == 'date' and r.value and r.current.startswith(r.value) and r.value != r.current:
            p.append(f'**Proposed value**: `{r.value}` is a less precise form of the published `{r.current}`, not a different date.')
    kind = KIND.get((f.get('evidence_kind') or '').strip())
    if not kind:
        p.append('**Kind of evidence**: choose one of the options in the form.')
    else:
        r.kind, r.weight = kind[0], kind[1]
    r.url = (f.get('evidence_url') or '').strip().strip('<>')
    if not URL.match(r.url) or len(r.url) > 500:
        p.append('**Link to the evidence**: give one link starting with `https://`.')
    elif OURS.match(r.url):
        p.append('**Link to the evidence**: a Title Screen page is not evidence for itself; link to where the value is written.')
    r.detail = (f.get('evidence_detail') or '').strip()
    if len(r.detail) < 30:
        p.append('**What the evidence shows**: say what it says and where, in a sentence or two.')
    boxes = re.findall(r'^- \[([ xX])\]', f.get('confirm') or '', flags=re.M)
    if len(boxes) < 2 or any(b == ' ' for b in boxes):
        p.append('**Checks**: both boxes must be ticked.')
    return r


def normalise(fact: str, raw: str, data: Data) -> Tuple[str, str, str]:
    """(value, display, problem): a proposal in the form the live data uses."""
    if not raw:
        return '', '', 'empty.'
    if fact == 'date':
        m = DATE.match(raw)
        if not m:
            return '', '', f'`{raw}` is not a date; write `YYYY`, `YYYY-MM` or `YYYY-MM-DD`.'
        y, mo, d = int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1)
        try:
            dt.date(y, mo, d)
        except ValueError:
            return '', '', f'`{raw}` is not a calendar date.'
        if not 1950 <= y <= dt.date.today().year + 1:
            return '', '', f'`{raw}` is outside the years Title Screen covers.'
        return raw, raw, ''
    if fact in ('publisher', 'developer'):
        m = COMPANY.match(raw)
        if m:
            c = data.company(m.group('id'))
            if not c:
                return '', '', f'`{m.group("id")}` is not a company id on titlescreen.org.'
            return c['id'], f"{c['name']} [{c['id']}]", ''
        m = NEW_COMPANY.match(raw)
        if m and len(fold(m.group('name'))) >= 2:
            name = m.group('name').strip()
            return 'new:' + fold(name), f'{name} (not listed yet)', ''
        return '', '', f'`{raw}` is neither `Name [c1234567]` (a company from the site\'s list) nor `new company: Name`.'
    if fact == 'genre':
        genres = data.genres()
        m = GENRE.search(raw)
        gid = m.group('id') if m else next((g for g, x in genres.items() if x['name'].lower() == raw.lower()), '')
        if gid not in genres:
            return '', '', f'`{raw}` is not a genre on titlescreen.org (see https://titlescreen.org/genres).'
        return gid, f"{genres[gid]['name']} [{gid}]", ''
    if fact == 'players':
        if not re.fullmatch(r'\d{1,2}', raw) or not 1 <= int(raw) <= 64:
            return '', '', f'`{raw}` is not a player count from 1 to 64.'
        return str(int(raw)), str(int(raw)), ''
    return '', '', 'unknown fact.'


# ---------- scoring ----------

def compatible(fact: str, a: str, b: str) -> bool:
    """Whether two values can both be true: one date a more precise form of the other, a genre and its parent."""
    if not a or not b:
        return False
    if fact == 'date':
        return a.startswith(b) or b.startswith(a)
    if fact == 'genre':
        return a == b or a.startswith(b + '/') or b.startswith(a + '/')
    return a == b


def statement_value(s: dict, fact: str) -> List[str]:
    v = str(s.get('value') or '')
    return [x for x in v.split(';') if x] if fact in ('publisher', 'developer') else [v]


@dataclass
class Group:
    """Every report proposing one value for one fact of one release."""
    reports: List[Report]
    conflicts: List['Group'] = field(default_factory=list)
    score: float = 0.0
    band: str = ''
    why: List[str] = field(default_factory=list)
    prior_rejected: List[int] = field(default_factory=list)

    @property
    def lead(self) -> Report:
        return self.reports[0]

    @property
    def open(self) -> List[Report]:
        return [r for r in self.reports if r.state == 'open']


def score(g: Group, data: Data, account_age: Dict[str, int]) -> None:
    lead = g.lead
    fact, value, rel = lead.fact, lead.value, lead.release
    why = g.why
    best = max(g.reports, key=lambda r: r.weight)
    s = best.weight
    if fact == 'genre' and s > GENRE_CAP:
        # a genre is a classification: a box or an advert shows how a game was sold, not which genre it is
        s = GENRE_CAP
        why.append(f'evidence: {KIND_BY_KEY[best.kind][1]}, counted as {GENRE_CAP:g} for a genre (+{GENRE_CAP:g})')
    else:
        why.append(f'evidence: {KIND_BY_KEY[best.kind][1]} (+{best.weight:g})')
    # do the sources already behind this release say it?
    field_name = API_FIELD[fact]
    says, tiers, broad = set(), [], True
    for st in data.evidence(rel['id']):
        if st.get('field') != field_name:
            continue
        vals = statement_value(st, fact)
        if any(compatible(fact, v, value) for v in vals) and not any(compatible(fact, v, lead.current) for v in vals):
            says.add(st.get('source', '?'))
            tiers.append(int(st.get('tier', 4)))
            # a date stated for the title or the game anywhere is not this market's date
            broad = broad and fact == 'date' and st.get('scope', 'release') != 'release'
    if says:
        bonus = 0.5 if broad else 2.0 if min(tiers) <= 2 else 1.0
        s += bonus
        why.append(f'{", ".join(sorted(says))} already state{"s" if len(says) == 1 else ""} it (+{bonus:g})')
    sup = (rel.get('support') or {}).get(field_name)
    if not lead.current:
        s += 0.5
        why.append('nothing is published for this fact yet (+0.5)')
    elif not sup:
        s += 0.5
        why.append('the published value has no measured support (+0.5)')
    else:
        if sup.get('against'):
            s += 1.0
            why.append(f"the published value is contested: {sup['agree']} for, {sup['against']} against (+1)")
        elif sup.get('agree') == 1:
            s += 0.5
            why.append('the published value rests on one source (+0.5)')
        if sup.get('joined') in ('name', 'main'):
            s += 0.5
            why.append(f"the published value was joined by {'title' if sup['joined'] == 'name' else 'main title'}, not an identifier (+0.5)")
        if sup.get('agree', 0) >= 3 and not sup.get('against') and sup.get('joined') == 'id':
            s -= 1.5
            why.append(f"{sup['agree']} sources joined by identifier agree on the published value (-1.5)")
    people = sorted({r.author for r in g.reports if r.author})
    if len(people) > 1:
        bonus = min(2.0, len(people) - 1.0)
        s += bonus
        why.append(f'{len(people)} people reported it (+{bonus:g})')
    if people and all(account_age.get(p, 9999) < NEW_ACCOUNT_DAYS for p in people):
        s -= 1.0
        why.append(f'every reporter\'s GitHub account is under {NEW_ACCOUNT_DAYS} days old (-1)')
    capped = []
    if lead.new_company:
        capped.append('names a company not listed yet')
    if g.conflicts:
        capped.append('other reports propose another value')
    if capped and s >= LIKELY:
        s = LIKELY - 0.1
        why.append(f'held below "likely accurate": {" and ".join(capped)}')
    g.score = round(s, 2)
    g.band = 'likely-accurate' if s >= LIKELY else 'plausible' if s >= PLAUSIBLE else 'weak-evidence'


def group(reports: List[Report]) -> List[Group]:
    """Reports on one release's fact with the same value are one group, oldest first; groups with values that
    cannot both be true are each other's conflicts."""
    by_fact: Dict[Tuple[str, str], List[Group]] = {}
    for r in sorted(reports, key=lambda r: (r.created, r.number)):
        gs = by_fact.setdefault((r.release['id'], r.fact), [])
        g = next((g for g in gs if g.lead.value == r.value), None)
        if g:
            g.reports.append(r)
        else:
            gs.append(Group([r]))
    out = []
    for (rid, fact), gs in by_fact.items():
        for g in gs:
            g.conflicts = [o for o in gs if o is not g and not compatible(fact, o.lead.value, g.lead.value)]
        out.extend(gs)
    return out


# ---------- the override a correction becomes ----------

def override(g: Group, data: Data, repo: str) -> Optional[str]:
    lead = g.lead
    if lead.new_company or not lead.release.get('natural_key'):
        return None
    if lead.fact in ('publisher', 'developer'):
        c = data.company(lead.value)
        value = json.dumps(c['key']) if c else None
    elif lead.fact == 'players':
        value = lead.value
    else:
        value = json.dumps(lead.value)
    if value is None:
        return None
    best = max(g.reports, key=lambda r: r.weight)
    refs = ', '.join(f'{repo}#{r.number}' for r in g.reports)
    reason = f'{KIND_BY_KEY[best.kind][1]}: {best.url} ({refs})'
    return (f'- release: {json.dumps(lead.release["natural_key"])}\n'
            f'  set: {{{OVERRIDE_FIELD[lead.fact]}: {value}}}\n'
            f'  reason: {json.dumps(reason)}')


# ---------- GitHub ----------

class GitHub:
    def __init__(self, repo: str = None, token: str = None, dry_run: bool = False) -> None:
        self.repo = repo or os.environ['GITHUB_REPOSITORY']
        self.token = token or os.environ['GITHUB_TOKEN']
        self.dry_run = dry_run
        self.done: List[str] = []

    def call(self, method: str, path: str, body=None):
        url = path if path.startswith('https://') else f'https://api.github.com{path}'
        req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'authorization': f'Bearer {self.token}', 'accept': 'application/vnd.github+json',
                                              'x-github-api-version': '2022-11-28', 'user-agent': 'titlescreen-corrections-bot'})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read()
            nxt = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get('link') or '')
        return (json.loads(text) if text else None), (nxt.group(1) if nxt else None)

    def pages(self, path: str) -> List[dict]:
        out, url = [], path
        while url:
            page, url = self.call('GET', url)
            out.extend(page)
        return out

    def write(self, what: str, method: str, path: str, body=None):
        self.done.append(what)
        if self.dry_run:
            print('dry run:', what)
            return None
        return self.call(method, path, body)[0]

    # the operations the bot makes
    def issue(self, n: int) -> dict:
        return self.call('GET', f'/repos/{self.repo}/issues/{n}')[0]

    def issues(self, label: str, state: str = 'all') -> List[dict]:
        return [i for i in self.pages(f'/repos/{self.repo}/issues?labels={urllib.parse.quote(label)}&state={state}&per_page=100')
                if 'pull_request' not in i]

    def comments(self, n: int) -> List[dict]:
        return self.pages(f'/repos/{self.repo}/issues/{n}/comments?per_page=100')

    def say(self, n: int, mark: str, text: str) -> None:
        """One comment per kind per issue, edited in place rather than added again."""
        body = f'{mark}\n{text}'
        mine = next((c for c in self.comments(n) if mark in (c.get('body') or '')), None)
        if mine and mine['body'].strip() == body.strip():
            return
        if mine:
            self.write(f'edit comment on #{n}', 'PATCH', f'/repos/{self.repo}/issues/comments/{mine["id"]}', {'body': body})
        else:
            self.write(f'comment on #{n}', 'POST', f'/repos/{self.repo}/issues/{n}/comments', {'body': body})

    def labels(self, issue: dict, add=(), remove=()) -> None:
        have = {l['name'] for l in issue.get('labels', [])}
        for l in add:
            if l not in have:
                self.write(f'label #{issue["number"]} +{l}', 'POST', f'/repos/{self.repo}/issues/{issue["number"]}/labels', {'labels': [l]})
        for l in remove:
            if l in have:
                self.write(f'label #{issue["number"]} -{l}', 'DELETE', f'/repos/{self.repo}/issues/{issue["number"]}/labels/{urllib.parse.quote(l)}')

    def close(self, n: int, reason: str) -> None:
        self.write(f'close #{n} ({reason})', 'PATCH', f'/repos/{self.repo}/issues/{n}', {'state': 'closed', 'state_reason': reason})

    def reopen(self, n: int) -> None:
        self.write(f'reopen #{n}', 'PATCH', f'/repos/{self.repo}/issues/{n}', {'state': 'open'})

    def ensure_labels(self) -> None:
        have = {l['name'] for l in self.pages(f'/repos/{self.repo}/labels?per_page=100')}
        for name, (color, desc) in LABELS.items():
            if name not in have:
                self.write(f'create label {name}', 'POST', f'/repos/{self.repo}/labels', {'name': name, 'color': color, 'description': desc})

    def account_age(self, login: str) -> int:
        try:
            u = self.call('GET', f'/users/{urllib.parse.quote(login)}')[0]
            made = dt.datetime.fromisoformat(u['created_at'].replace('Z', '+00:00'))
            return (dt.datetime.now(dt.timezone.utc) - made).days
        except Exception:
            return 9999


# ---------- the two commands ----------

def check(gh: GitHub, data: Data, n: int) -> None:
    issue = gh.issue(n)
    if not is_correction(issue) or 'digest' in {l['name'] for l in issue.get('labels', [])}:
        print(f'#{n} is not a correction')
        return
    r = read(issue, data)
    if not r.problems and r.value and r.value == r.current:
        r.problems.append(f'**Proposed value**: `{r.display}` is the value published now.')
    if r.problems:
        gh.say(n, MARK_CHECK, 'This correction cannot be reviewed yet:\n\n' + '\n'.join(f'- {p}' for p in r.problems)
               + '\n\nEdit the issue to fix these and it is checked again and reopened. Starting from the release\'s page on '
                 'titlescreen.org (*Suggest a correction*) fills the fields in correctly.')
        gh.labels(issue, add=['correction', 'invalid'], remove=['needs-review', *BANDS])
        if issue['state'] == 'open':
            gh.close(n, 'not_planned')
        return
    gh.labels(issue, add=['correction', 'needs-review'], remove=['invalid'])
    note = f'Checks passed: **{r.fact}** of release `{r.release["id"]}`, `{r.current_display or "(not known)"}` -> `{r.display}`. ' \
           'Corrections are scored once a week and the best supported go to a person to check against the evidence.'
    if r.new_company:
        note += ' It names a company Title Screen does not list yet, so a person adds the company first.'
    gh.say(n, MARK_CHECK, note)
    if issue['state'] == 'closed' and 'invalid' in r.labels:
        gh.reopen(n)


def review(gh: GitHub, data: Data) -> str:
    gh.ensure_labels()
    issues = [i for i in gh.issues('correction') if 'digest' not in {l['name'] for l in i.get('labels', [])}]
    version = data.version()
    reports, closed_now = [], []
    for issue in issues:
        names = {l['name'] for l in issue.get('labels', [])}
        # open reports; closed ones only as roll-ups (their reporters still count) or as a past rejection
        if issue['state'] == 'closed' and not names & {'duplicate', 'rejected'}:
            continue
        r = read(issue, data)
        if r.problems:
            if issue['state'] == 'open':
                check(gh, data, issue['number'])
                closed_now.append(f'#{r.number} closed: a field did not check out')
            continue
        reports.append(r)
    rejected = [r for r in reports if 'rejected' in r.labels]
    reports = [r for r in reports if 'rejected' not in r.labels]
    groups = [g for g in group(reports) if g.open]
    ages: Dict[str, int] = {}
    for g in groups:
        for r in g.reports:
            if r.author and r.author not in ages:
                ages[r.author] = gh.account_age(r.author)
    live = []
    for g in groups:
        lead = g.lead
        if lead.value == lead.current:
            for r in g.open:
                gh.say(r.number, MARK_ROLLUP, f'Build `{version}` publishes `{lead.display}` for this fact. Thank you.')
                gh.labels({'number': r.number, 'labels': [{'name': x} for x in r.labels]},
                          remove=['needs-review', 'conflicting', *BANDS])
                gh.close(r.number, 'completed')
                closed_now.append(f'#{r.number} applied in build {version}')
            continue
        # the oldest open report leads; later ones roll up into it
        lead = g.open[0]
        g.reports.remove(lead)
        g.reports.insert(0, lead)
        for r in g.open[1:]:
            gh.say(r.number, MARK_ROLLUP, f'Rolled up into #{lead.number}, which proposes the same value and now counts your '
                   'report and evidence. Follow that issue for the outcome.')
            gh.labels({'number': r.number, 'labels': [{'name': x} for x in r.labels]}, add=['duplicate'],
                      remove=['needs-review', 'conflicting', *BANDS])
            gh.close(r.number, 'not_planned')
            closed_now.append(f'#{r.number} rolled up into #{lead.number}')
        g.prior_rejected = [x.number for x in rejected if x.release['id'] == lead.release['id'] and x.fact == lead.fact
                            and x.value == lead.value]
        score(g, data, ages)
        live.append(g)
        others = g.reports[1:]
        lines = [f'**Review** (build `{version}`): {BANDS[g.band].lower()}, score {g.score:g}.', '']
        lines += [f'- {w}' for w in g.why]
        if others:
            lines += ['', 'Also reported in ' + ', '.join(f'#{r.number}' for r in others) + '. Their evidence:']
            lines += [f'- #{r.number}: {KIND_BY_KEY[r.kind][1]}, {r.url}' for r in others]
        if g.conflicts:
            lines += ['', 'Other reports propose a different value: ' + ', '.join(f'#{c.lead.number} (`{c.lead.display}`)' for c in g.conflicts)]
        if g.prior_rejected:
            lines += ['', 'The same value was proposed and rejected before: ' + ', '.join(f'#{x}' for x in g.prior_rejected)
                      + '. New evidence is welcome; say what is new.']
        lines += ['', 'The score ranks reports for a person to check; it applies nothing. How it is worked out: '
                  'https://titlescreen.org/corrections']
        gh.say(lead.number, MARK_ROLLUP, '\n'.join(lines))
        issue = {'number': lead.number, 'labels': [{'name': x} for x in lead.labels]}
        gh.labels(issue, add=[g.band] + (['conflicting'] if g.conflicts else []) + (['new-company'] if lead.new_company else []),
                  remove=['needs-review'] + [b for b in BANDS if b != g.band] + ([] if g.conflicts else ['conflicting']))
    body = digest(live, closed_now, version, data, gh.repo)
    old = next((i for i in gh.issues('digest', 'open')), None)
    url = f'https://github.com/{gh.repo}/issues?q=label%3Adigest'
    if old:
        url = old.get('html_url') or url
        if (old.get('body') or '').split('\n', 2)[2:] != body.split('\n', 2)[2:]:
            gh.write('update digest', 'PATCH', f'/repos/{gh.repo}/issues/{old["number"]}', {'body': body})
    else:
        made = gh.write('create digest', 'POST', f'/repos/{gh.repo}/issues', {'title': 'Corrections digest', 'body': body, 'labels': ['digest']})
        url = (made or {}).get('html_url') or url
    notify(live, url, version, dry_run=gh.dry_run)
    return body


# ---------- Herald ----------

HERALD_TOPIC = 'titlescreen.corrections'          # something waits on a person
HERALD_BEAT = 'titlescreen.corrections.bot'       # the bot ran (fed every run; a dead-man there watches it)
WEEK = 7 * 86400
DEADMAN = {'expectEvery': '7d', 'grace': '1d', 'priority': 'high'}


def herald(topic: str, event: dict, path: str = 'pub', method: str = 'POST') -> None:
    base = os.environ.get('HERALD_URL', 'https://herald.arinbakht.ca').rstrip('/')
    req = urllib.request.Request(f'{base}/{path}/{topic}', method=method, data=json.dumps(event).encode(),
                                 headers={'authorization': f'Bearer {os.environ["HERALD_TOKEN"]}',
                                          'content-type': 'application/json', 'user-agent': 'titlescreen-corrections-bot'})
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


def notify(groups: List[Group], digest_url: str, version: str, dry_run: bool = False) -> Optional[dict]:
    """The heartbeat, and the notification when corrections wait to be accepted or rejected, or accepted ones
    wait for a build. One a week while anything waits, expiring after a week: no dedupe, so a week ignored does
    not swallow the next one."""
    decide = [g for g in groups if 'accepted' not in g.lead.labels]
    build = [g for g in groups if 'accepted' in g.lead.labels]
    beat = {'title': 'Corrections review ran', 'body': f'{len(decide)} to decide, {len(build)} accepted, build {version}',
            'priority': 'low', 'ttl': 2 * 86400}
    event = None
    if decide or build:
        parts = []
        if decide:
            bands = [(sum(g.band == b for g in decide), t.lower()) for b, t in BANDS.items()]
            parts.append(f'{len(decide)} to accept or reject (' + ', '.join(f'{n} {t}' for n, t in bands if n) + ')')
        if build:
            parts.append(f'{len(build)} accepted, waiting for tools/apply_corrections.py and a build')
        n = len(decide) or len(build)
        title = (f'Title Screen: {n} correction{"s" if n != 1 else ""} to review' if decide
                 else f'Title Screen: {n} accepted correction{"s" if n != 1 else ""} to build')
        event = {'title': title, 'body': '; '.join(parts) + '.', 'priority': 'normal', 'ttl': WEEK,
                 'actions': [{'label': 'Open the digest', 'url': digest_url}]}
    if dry_run or not os.environ.get('HERALD_TOKEN'):
        print('herald (not sent):', json.dumps(beat), json.dumps(event) if event else 'no notification')
        return event
    # the dead-man on the heartbeat, (re)registered by the bot itself: a week and a day of silence alerts
    herald(HERALD_BEAT, DEADMAN, path='deadman', method='PUT')
    herald(HERALD_BEAT, beat)
    if event:
        herald(HERALD_TOPIC, event)
    print('herald:', event['title'] if event else 'heartbeat only')
    return event


def digest(groups: List[Group], closed_now: List[str], version: str, data: Data, repo: str) -> str:
    now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    out = [MARK_DIGEST, f'Updated {now} against build `{version}`: {len(groups)} open corrections, '
           f'{sum(len(g.reports) for g in groups)} reports. Rules: https://titlescreen.org/corrections', '']

    def table(gs: List[Group]) -> List[str]:
        rows = ['| Issue | Release | Fact | Published | Proposed | Score | Why |', '|---|---|---|---|---|---|---|']
        for g in gs[:100]:
            l = g.lead
            t = (l.release.get('title') or l.release.get('title_latin') or '').replace('|', '/')
            more = f' +{len(g.reports) - 1}' if len(g.reports) > 1 else ''
            flags = (' ⚠ conflicting' if g.conflicts else '') + (' · new company' if l.new_company else '') \
                + (f' · rejected before {", ".join(f"#{x}" for x in g.prior_rejected)}' if g.prior_rejected else '')
            rows.append(f'| #{l.number}{more} | [{t}](https://titlescreen.org/release/{l.release["id"]}) {l.release.get("platform_id", "")} '
                        f'{l.release.get("region", "")} | {l.fact} | `{l.current_display or "-"}` | `{l.display}` | {g.score:g} | '
                        f'{"; ".join(w.split(" (")[0] for w in g.why[:3])}{flags} |')
        if len(gs) > 100:
            rows.append(f'| ... {len(gs) - 100} more | | | | | | |')
        return rows

    def overrides(gs: List[Group], tag: str) -> List[str]:
        ys = [y for y in (override(g, data, repo) for g in gs) if y]
        if not ys:
            return []
        return ['', f'<details><summary>Overrides ({len(ys)})</summary>', '', f'<!-- overrides:{tag} -->', '```yaml', *ys, '```',
                f'<!-- /overrides:{tag} -->', '</details>']

    order = lambda gs: sorted(gs, key=lambda g: (-g.score, g.lead.number))
    accepted = order([g for g in groups if 'accepted' in g.lead.labels])
    rest = [g for g in groups if 'accepted' not in g.lead.labels]
    if accepted:
        out += [f'## Accepted, waiting for a build ({len(accepted)})', '', *table(accepted), *overrides(accepted, 'accepted'), '']
    for band, title in BANDS.items():
        gs = order([g for g in rest if g.band == band])
        out += [f'## {title} ({len(gs)})', '']
        out += (table(gs) + (overrides(gs, band) if band == 'likely-accurate' else [])) if gs else ['None.']
        out.append('')
    if closed_now:
        out += [f'## Closed this run ({len(closed_now)})', '', *[f'- {c}' for c in closed_now[:200]], '']
    out += ['To accept one: check its evidence, add the `accepted` label; its override moves to the block above for the build. '
            'To reject: add `rejected` with a comment saying why, and close it.']
    body = '\n'.join(out)
    return body if len(body) < 65000 else body[:64000] + '\n\n(truncated)'


def main(argv: List[str]) -> int:
    if len(argv) < 2 or argv[1] not in ('check', 'review'):
        print(__doc__)
        return 2
    gh = GitHub(dry_run='--dry-run' in argv)
    data = Data()
    if argv[1] == 'check':
        check(gh, data, int(argv[2]))
    else:
        body = review(gh, data)
        if gh.dry_run:
            print(body)
    print(f'{len(gh.done)} changes')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
