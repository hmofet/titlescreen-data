"""Tests for review.py against a fake API and a fake GitHub:  python -m unittest tools/corrections/test_review.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import review as R  # noqa: E402

MEDIA = R.EVIDENCE[0][1]
DATABASE = R.EVIDENCE[-1][1]

RELEASE = {
    'id': 'rve3nkew', 'title': 'Sky Kid', 'platform_id': 'nes', 'region': 'JP', 'year': '1986', 'month': 8, 'day': 22,
    'publisher_id': 'c85wgm9g', 'developer_id': 'c85wgm9g', 'genre_id': 'shooter/shmup', 'players_max': 2,
    'natural_key': 'nes|sky kid|JP|commercial',
    'support': {'date': {'agree': 3, 'against': 1, 'joined': 'id'}, 'publisher': {'agree': 1, 'against': 0, 'joined': 'name'},
                'developer': {'agree': 4, 'against': 0, 'joined': 'id'}, 'genre': {'agree': 1, 'against': 2, 'joined': 'id'},
                'players_max': {'agree': 2, 'against': 0, 'joined': 'id'}},
}
EVIDENCE = [
    {'field': 'publisher', 'value': 'c85wgm9g', 'source': 'libretro', 'tier': 2},
    {'field': 'publisher', 'value': 'csunsoft', 'source': 'wikidata', 'tier': 4},
    {'field': 'date', 'value': '1985-12', 'source': 'wikidata', 'tier': 4, 'scope': 'title'},
    {'field': 'genre', 'value': 'action', 'source': 'launchbox', 'tier': 4, 'scope': 'title'},
]


class FakeData(R.Data):
    def __init__(self):
        super().__init__(base='http://fake', pause=0)
        self.store = {
            'releases/rve3nkew': RELEASE, 'releases/rve3nkew/evidence': EVIDENCE,
            'companies/c85wgm9g': {'id': 'c85wgm9g', 'name': 'Namco', 'key': 'namco'},
            'companies/csunsoft': {'id': 'csunsoft', 'name': 'Sunsoft', 'key': 'sunsoft'},
            'genres': [{'id': 'shooter', 'name': 'Shooter'}, {'id': 'shooter/shmup', 'name': "Shoot 'em up", 'parent': 'shooter'},
                       {'id': 'action', 'name': 'Action'}],
            'meta': {'version': '2026.10.09'},
        }

    def get(self, path):
        return self.store.get(path)


def body(release='rve3nkew', fact='publisher', proposed='Sunsoft [csunsoft]', kind=MEDIA,
         url='https://example.org/skykid-label.jpg', detail='The cartridge label reads Sunsoft at the bottom edge.', boxes='XX'):
    b1, b2 = boxes
    return '\n\n'.join([
        f'### Release\n\n{release}', f'### Fact\n\n{fact}', '### Published now\n\nPublished: Namco [c85wgm9g]',
        f'### Proposed value\n\n{proposed}', f'### Kind of evidence\n\n{kind}', f'### Link to the evidence\n\n{url}',
        f'### What the evidence shows\n\n{detail}',
        f'### Checks\n\n- [{b1}] I looked at the evidence myself\n- [{b2}] I agree to CC BY 4.0'])


def issue(n, author='alice', created=None, state='open', labels=('correction',), **kw):
    return {'number': n, 'user': {'login': author}, 'created_at': created or f'2026-10-{n:02d}T00:00:00Z', 'state': state,
            'labels': [{'name': l} for l in labels], 'body': body(**kw), 'title': 'Correction: x'}


class FakeGitHub(R.GitHub):
    def __init__(self, issues, ages=None):
        super().__init__(repo='hmofet/titlescreen-data', token='x')
        self.items = {i['number']: i for i in issues}
        self.notes = {}
        self.ages = ages or {}

    def issue(self, n):
        return self.items[n]

    def issues(self, label, state='all'):
        return [i for i in self.items.values() if label in {l['name'] for l in i['labels']} and state in ('all', i['state'])]

    def comments(self, n):
        return [{'id': (n, k), 'body': v} for k, v in self.notes.get(n, {}).items()]

    def account_age(self, login):
        return self.ages.get(login, 1000)

    def ensure_labels(self):
        pass

    def write(self, what, method, path, body=None):
        self.done.append(what)
        parts = path.split('/')
        if path.endswith('/comments') and method == 'POST':
            n = int(parts[-2])
            self.notes.setdefault(n, {})[body['body'].split('\n')[0]] = body['body']
        elif path.endswith('/labels') and method == 'POST':
            self.items[int(parts[-2])]['labels'].append({'name': body['labels'][0]})
        elif '/labels/' in path and method == 'DELETE':
            it = self.items[int(parts[-3])]
            it['labels'] = [l for l in it['labels'] if l['name'] != R.urllib.parse.unquote(parts[-1])]
        elif method == 'PATCH' and parts[-2] == 'issues':
            it = self.items[int(parts[-1])]
            it.update({k: v for k, v in body.items() if k in ('state', 'body')})
        elif method == 'POST' and path.endswith('/issues'):
            n = max(self.items, default=0) + 1
            self.items[n] = {'number': n, 'state': 'open', 'labels': [{'name': 'digest'}], 'body': body['body'], 'user': {'login': 'bot'}}
        return None

    def say(self, n, mark, text):
        self.notes.setdefault(n, {})[mark] = f'{mark}\n{text}'
        self.done.append(f'say #{n}')

    def labels_of(self, n):
        return {l['name'] for l in self.items[n]['labels']}


class ParseAndCheck(unittest.TestCase):
    def test_parse_body(self):
        f = R.parse_body(body())
        self.assertEqual(f['release'], 'rve3nkew')
        self.assertEqual(f['evidence_kind'], MEDIA)
        self.assertIn('- [X]', f['confirm'])

    def test_good_report(self):
        r = R.read(issue(1), FakeData())
        self.assertEqual(r.problems, [])
        self.assertEqual((r.value, r.current, r.kind), ('csunsoft', 'c85wgm9g', 'media'))

    def test_tampered_fields(self):
        cases = {
            'release': dict(release='rnotthere'), 'fact': dict(fact='franchise'),
            'company': dict(proposed='Sunsoft'), 'company id': dict(proposed='Sunsoft [c0000000]'),
            'kind': dict(kind='My memory'), 'url': dict(url='see the box'), 'ours': dict(url='https://titlescreen.org/release/rve3nkew'),
            'detail': dict(detail='trust me'), 'boxes': dict(boxes='X '),
            'date': dict(fact='date', proposed='1986-02-30'), 'players': dict(fact='players', proposed='999'),
            'genre': dict(fact='genre', proposed='Shmup [shmup]'), 'vaguer date': dict(fact='date', proposed='1986-08'),
        }
        for name, kw in cases.items():
            with self.subTest(name):
                self.assertTrue(R.read(issue(1, **kw), FakeData()).problems, name)

    def test_values(self):
        d = FakeData()
        self.assertEqual(R.normalise('date', '1986-09', d)[0], '1986-09')
        self.assertEqual(R.normalise('genre', 'Action [action]', d)[0], 'action')
        self.assertEqual(R.normalise('genre', 'shooter', d)[0], 'shooter')
        self.assertEqual(R.normalise('publisher', 'new company: Hudson Soft', d)[0], 'new:hudsonsoft')
        self.assertEqual(R.normalise('players', '04', d)[0], '4')

    def test_check_closes_and_reopens(self):
        gh = FakeGitHub([issue(1, url='nope')])
        R.check(gh, FakeData(), 1)
        self.assertEqual(gh.items[1]['state'], 'closed')
        self.assertIn('invalid', gh.labels_of(1))
        gh.items[1]['body'] = body()
        R.check(gh, FakeData(), 1)
        self.assertEqual(gh.items[1]['state'], 'open')
        self.assertEqual(gh.labels_of(1), {'correction', 'needs-review'})

    def test_check_refuses_the_published_value(self):
        gh = FakeGitHub([issue(1, proposed='Namco [c85wgm9g]')])
        R.check(gh, FakeData(), 1)
        self.assertEqual(gh.items[1]['state'], 'closed')

    def test_check_ignores_other_issues(self):
        gh = FakeGitHub([{'number': 1, 'labels': [], 'body': 'the archive download is slow', 'state': 'open'}])
        R.check(gh, FakeData(), 1)
        self.assertEqual(gh.done, [])


class Review(unittest.TestCase):
    def test_duplicates_roll_up_into_the_oldest(self):
        gh = FakeGitHub([issue(1, author='alice'), issue(2, author='bob', kind=DATABASE), issue(3, author='carol')])
        R.review(gh, FakeData())
        self.assertEqual(gh.items[1]['state'], 'open')
        for n in (2, 3):
            self.assertEqual(gh.items[n]['state'], 'closed')
            self.assertIn('duplicate', gh.labels_of(n))
        note = gh.notes[1][R.MARK_ROLLUP]
        self.assertIn('3 people reported it', note)
        self.assertIn('#2', note)

    def test_scores(self):
        # media evidence (3) + wikidata already says Sunsoft (1) + one-source published (0.5) + title join (0.5) = 5
        gh = FakeGitHub([issue(1)])
        R.review(gh, FakeData())
        self.assertIn('likely-accurate', gh.labels_of(1))
        # a wiki link on a strongly supported fact: 0.5 - 1.5
        gh = FakeGitHub([issue(1, fact='developer', proposed='Sunsoft [csunsoft]', kind=DATABASE)])
        R.review(gh, FakeData())
        self.assertIn('weak-evidence', gh.labels_of(1))

    def test_genre_and_broad_dates_count_less(self):
        # a box photo for a genre counts 1.5; + launchbox says it (1) + contested (1) = 3.5
        gh = FakeGitHub([issue(1, fact='genre', proposed='Action [action]')])
        R.review(gh, FakeData())
        self.assertIn('plausible', gh.labels_of(1))
        # a magazine (2) + wikidata's date for the title, not this market (0.5) + contested (1) = 3.5
        gh = FakeGitHub([issue(1, fact='date', proposed='1985-12', kind=R.EVIDENCE[3][1])])
        R.review(gh, FakeData())
        self.assertIn('plausible', gh.labels_of(1))
        self.assertIn('(+0.5)', gh.notes[1][R.MARK_ROLLUP])

    def test_new_accounts_count_less(self):
        gh = FakeGitHub([issue(1)], ages={'alice': 2})
        R.review(gh, FakeData())
        self.assertIn('under 30 days', gh.notes[1][R.MARK_ROLLUP])

    def test_conflicts_are_flagged_and_held_back(self):
        gh = FakeGitHub([issue(1), issue(2, author='bob', proposed='new company: Namcot')])
        R.review(gh, FakeData())
        for n in (1, 2):
            self.assertIn('conflicting', gh.labels_of(n))
            self.assertNotIn('likely-accurate', gh.labels_of(n))

    def test_compatible_dates_do_not_conflict(self):
        gh = FakeGitHub([issue(1, fact='date', proposed='1985'), issue(2, author='bob', fact='date', proposed='1985-12-07')])
        R.review(gh, FakeData())
        self.assertNotIn('conflicting', gh.labels_of(1))
        self.assertEqual(gh.items[2]['state'], 'open')

    def test_applied_corrections_close(self):
        d = FakeData()
        d.store['releases/rve3nkew'] = dict(RELEASE, publisher_id='csunsoft')
        gh = FakeGitHub([issue(1)])
        R.review(gh, d)
        self.assertEqual(gh.items[1]['state'], 'closed')
        self.assertIn('2026.10.09', gh.notes[1][R.MARK_ROLLUP])

    def test_digest_and_override(self):
        gh = FakeGitHub([issue(1), issue(2, fact='players', proposed='4', kind=DATABASE, author='bob')])
        gh.items[1]['labels'].append({'name': 'accepted'})
        R.review(gh, FakeData())
        digest = next(i for i in gh.items.values() if 'digest' in {l['name'] for l in i['labels']})
        b = digest['body']
        self.assertIn('## Accepted, waiting for a build (1)', b)
        self.assertIn('- release: "nes|sky kid|JP|commercial"\n  set: {publisher: "sunsoft"}', b)
        self.assertIn('<!-- overrides:accepted -->', b)
        # a second run edits the same digest rather than opening another
        R.review(gh, FakeData())
        self.assertEqual(sum('digest' in {l['name'] for l in i['labels']} for i in gh.items.values()), 1)

    def test_rejected_value_is_pointed_out(self):
        gh = FakeGitHub([issue(1, state='closed', labels=('correction', 'rejected')), issue(2, author='bob')])
        R.review(gh, FakeData())
        self.assertIn('rejected before', gh.notes[2][R.MARK_ROLLUP])


class Herald(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.real = R.herald
        R.herald = lambda topic, event, path='pub', method='POST': self.sent.append((topic, event) if path == 'pub' else ('deadman', event))
        os.environ['HERALD_TOKEN'] = 'x'

    def tearDown(self):
        R.herald = self.real
        os.environ.pop('HERALD_TOKEN', None)

    def test_quiet_week_only_beats(self):
        R.review(FakeGitHub([]), FakeData())
        self.assertEqual([t for t, _ in self.sent], ['deadman', R.HERALD_BEAT])
        self.assertEqual(self.sent[0][1]['expectEvery'], '7d')
        self.assertEqual(self.sent[1][1]['priority'], 'low')

    def test_waiting_corrections_notify(self):
        gh = FakeGitHub([issue(1), issue(2, fact='players', proposed='4', author='bob', kind=DATABASE)])
        R.review(gh, FakeData())
        topic, ev = self.sent[-1]
        self.assertEqual(topic, R.HERALD_TOPIC)
        self.assertEqual(ev['title'], 'Title Screen: 2 corrections to review')
        self.assertIn('1 likely accurate', ev['body'])
        self.assertNotIn('dedupe', ev)
        self.assertEqual(ev['ttl'], R.WEEK)
        self.assertTrue(ev['actions'][0]['url'].startswith('https://github.com/'))

    def test_accepted_waiting_for_a_build_notifies(self):
        gh = FakeGitHub([issue(1)])
        gh.items[1]['labels'].append({'name': 'accepted'})
        R.review(gh, FakeData())
        self.assertEqual(self.sent[-1][1]['title'], 'Title Screen: 1 accepted correction to build')

    def test_no_token_sends_nothing(self):
        del os.environ['HERALD_TOKEN']
        R.review(FakeGitHub([issue(1)]), FakeData())
        self.assertEqual(self.sent, [])


if __name__ == '__main__':
    unittest.main()
