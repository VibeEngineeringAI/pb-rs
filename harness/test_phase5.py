"""Independent phase-five corpus, comparator, renderer and fixture assertions (no live service)."""
import base64
import copy
import http.client
import json
from pathlib import Path
import random
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import phase5  # noqa: E402

GO = phase5.ROOT.parent / 'bin' / 'pb-go-pinned'


def line(method, target):
    return {'method': method, 'target': target, 'headers': {}, 'body_b64': '', 'framing': 'none'}


class DeadlineNormalizationTests(unittest.TestCase):
    def test_observed_body_deadline_race_is_narrowly_scoped(self):
        case = next(c for c in phase5.corpus() if c['name'] == 'p5-user-add-stalled-body-deadline')
        tail = b'Usage:\n  pb user add user-name [flags]\n'
        bare = b'Error: context deadline exceeded\n' + tail
        annotated = b'Error: context deadline exceeded (Client.Timeout or context cancellation while reading body)\n' + tail
        self.assertEqual(phase5.normalize_timeout(case, bare), phase5.normalize_timeout(case, annotated))
        self.assertEqual(phase5.normalize_timeout({**case, 'tier': 'gate'}, bare), bare)
        self.assertEqual(phase5.normalize_timeout({**case, 'name': 'unrelated'}, bare), bare)
        unrelated = b'Error: another context deadline exceeded\n' + tail
        self.assertEqual(phase5.normalize_timeout(case, unrelated), unrelated)


class FanoutComparatorTests(unittest.TestCase):
    PATTERNS = [phase5.USER_FANOUT]

    def canon(self, items):
        return phase5.fanout_canonical(items, self.PATTERNS, phase5.contract_line)

    def sequence(self, order=(0, 1, 2)):
        details = [line('GET', f'/api/v1/user/u{i}/role') for i in range(3)]
        return [line('GET', '/api/v1/user')] + [details[i] for i in order]

    def test_sibling_arrival_order_is_ignored(self):
        self.assertEqual(self.canon(self.sequence((2, 0, 1))), self.canon(self.sequence()))

    def test_barrier_membership_and_multiplicity_are_kept(self):
        base = self.canon(self.sequence())
        details_first = self.sequence()[1:] + self.sequence()[:1]
        self.assertNotEqual(self.canon(details_first), base)
        missing = self.sequence()[:-1]
        self.assertNotEqual(self.canon(missing), base)
        doubled = self.sequence() + [line('GET', '/api/v1/user/u0/role')]
        self.assertNotEqual(self.canon(doubled), base)

    def test_headers_and_bodies_stay_exact_inside_a_run(self):
        changed = self.sequence()
        changed[2] = {**changed[2], 'headers': {'cookie': 'session=other'}}
        self.assertNotEqual(self.canon(changed), self.canon(self.sequence()))

    def test_refresh_position_is_causal(self):
        token = line('POST', phase5.TOKEN_PATH)
        old = [dict(line('GET', f'/api/v1/user/u{i}/role'), headers={'cookie': 'old'}) for i in range(2)]
        new = [dict(line('GET', f'/api/v1/user/u{i}/role'), headers={'cookie': 'new'}) for i in range(2)]
        good = [line('GET', '/api/v1/user'), old[1], old[0], token, new[0], new[1]]
        swapped = [line('GET', '/api/v1/user'), old[0], old[1], token, new[1], new[0]]
        self.assertEqual(self.canon(good), self.canon(swapped))
        # A replay that crosses the refresh, or a refresh inside the fan-out, is a real difference.
        crossed = [line('GET', '/api/v1/user'), old[0], new[1], token, old[1], new[0]]
        self.assertNotEqual(self.canon(crossed), self.canon(good))

    def test_non_get_and_non_matching_paths_are_never_reordered(self):
        items = [line('DELETE', '/api/v1/user/b/role'), line('DELETE', '/api/v1/user/a/role')]
        self.assertEqual(self.canon(items), items)
        self.assertEqual(phase5.fanout_canonical(['GET /x', 'GET /a'], []), ['GET /x', 'GET /a'])

    def test_role_pattern_does_not_absorb_roles_inventory(self):
        items = ['GET /api/v1/role/b', 'GET /api/v1/roles', 'GET /api/v1/role/a']
        self.assertEqual(phase5.fanout_canonical(items, [phase5.ROLE_FANOUT]), items)


class OutputCanonTests(unittest.TestCase):
    def test_user_text_sorts_only_role_cells(self):
        a = phase5.user_table([('x', ['b', 'a', 'a'], None), ('y', None, 'boom, bang')])
        b = phase5.user_table([('x', ['a', 'b', 'a'], None), ('y', None, 'boom, bang')])
        self.assertNotEqual(a, b)
        self.assertEqual(phase5.canon_user_text(a), phase5.canon_user_text(b))
        reordered = phase5.user_table([('y', None, 'boom, bang'), ('x', ['a', 'b', 'a'], None)])
        self.assertNotEqual(phase5.canon_user_text(reordered), phase5.canon_user_text(a))
        fewer = phase5.user_table([('x', ['a', 'b'], None), ('y', None, 'boom, bang')])
        self.assertNotEqual(phase5.canon_user_text(fewer), phase5.canon_user_text(a))

    def test_user_json_keeps_user_order_and_multiplicity(self):
        a = phase5.go_json([{'id': 'x', 'roles': ['b', 'a', 'a']}, {'id': 'y', 'roles': []}])
        b = phase5.go_json([{'id': 'x', 'roles': ['a', 'a', 'b']}, {'id': 'y', 'roles': []}])
        self.assertEqual(phase5.canon_user_json(a), phase5.canon_user_json(b))
        self.assertNotEqual(phase5.canon_user_json(a), phase5.canon_user_json(
            phase5.go_json([{'id': 'y', 'roles': []}, {'id': 'x', 'roles': ['a', 'a', 'b']}])))
        self.assertNotEqual(phase5.canon_user_json(a), phase5.canon_user_json(
            phase5.go_json([{'id': 'x', 'roles': ['a', 'b']}, {'id': 'y', 'roles': []}])))

    def test_user_json_preserves_encoding_and_object_order(self):
        value = [{'id': '<&>é', 'roles': ['a', 'b']}]
        expected = phase5.canon_user_json(phase5.go_json(value))
        changed = [json.dumps(value).encode(), phase5.go_json(value).rstrip(),
                   phase5.go_json(value).replace(b'<', b'\\u003c'),
                   phase5.go_json([{'roles': ['a', 'b'], 'id': '<&>é'}])]
        for data in changed:
            self.assertNotEqual(expected, phase5.canon_user_json(data))
        self.assertEqual(phase5.go_json(['\u2028\u2029']), b'[\n  "\\u2028\\u2029"\n]\n')

    def test_role_text_blocks_follow_map_order_but_actions_do_not(self):
        roles = [(n, a, None) for n, a in phase5.ROLES.items()]
        shuffled = roles[:]
        random.Random(5).shuffle(shuffled)
        self.assertEqual(phase5.canon_role_text(phase5.role_table(roles)),
                         phase5.canon_role_text(phase5.role_table(shuffled)))
        swapped = [(n, list(reversed(a)), e) for n, a, e in roles]
        self.assertNotEqual(phase5.canon_role_text(phase5.role_table(roles)),
                            phase5.canon_role_text(phase5.role_table(swapped)))

    def test_multiline_error_cell_stays_with_its_row(self):
        table = (b'\nROLE  P  D\n---\nb ..... - .... error: x   \nstatus 500\nbody   \n'
                 b'a ..... admin .... -\n\n')
        canon = phase5.canon_role_text(table)
        self.assertIn(b'b ..... - .... error: x   \nstatus 500\nbody   \n', canon)
        self.assertTrue(canon.index(b'a .....') < canon.index(b'b .....'))

    def test_role_errors_sort_causes_in_text_and_json(self):
        text = (b'Error: failed to fetch details for 2 role(s): Error fetching role data for z: a\nb; '
                b'Error fetching role data for a: c\n')
        flipped = (b'Error: failed to fetch details for 2 role(s): Error fetching role data for a: c; '
                   b'Error fetching role data for z: a\nb\n')
        self.assertEqual(phase5.canon_role_errors(text), phase5.canon_role_errors(flipped))
        self.assertNotEqual(phase5.canon_role_errors(text), phase5.canon_role_errors(flipped.replace(b': c', b': d')))
        message = 'failed to fetch details for 2 role(s): Error fetching role data for z: a\nb; Error fetching role data for a: c'
        other = 'failed to fetch details for 2 role(s): Error fetching role data for a: c; Error fetching role data for z: a\nb'
        envelope = lambda m: phase5.go_json({'error': {'code': 'COMMAND_FAILED', 'message': m, 'retryable': False}})
        self.assertEqual(phase5.canon_role_errors(envelope(message)), phase5.canon_role_errors(envelope(other)))
        self.assertEqual(phase5.canon_role_errors(b'unrelated; b; a'), b'unrelated; b; a')


class RendererModelTests(unittest.TestCase):
    def test_user_table_widths_and_truncation(self):
        out = phase5.user_table([('alice', ['admin'], None), ('界' * 40, [], None)]).decode()
        lines = out.split('\n')
        self.assertEqual(lines[0], '')
        self.assertEqual(lines[1], 'USER' + ' ' * 64 + 'ROLES')
        self.assertEqual(lines[2], '─' * 73)
        self.assertTrue(lines[4].startswith('界' * 31 + '…'))
        self.assertTrue(lines[4].endswith(' -'))
        self.assertEqual(out[-2:], '\n\n')

    def test_role_table_dataset_precedence_and_dash(self):
        out = phase5.role_table([('r', [{'privilege': ' ', 'resource': {'dataset': 'd', 'stream': 's'}},
                                        {'privilege': 'x', 'resource': {'stream': 's'}},
                                        {'privilege': 'y', 'resource': {}}], None)]).decode().split('\n')
        self.assertEqual(out[3], 'r ...... - ........... d')
        self.assertEqual(out[4], '         x ........... s')
        self.assertEqual(out[5], '         y ........... -')

    def test_role_json_keeps_null_empty_and_both_resource_fields(self):
        data = json.loads(phase5.role_json({'b': None, 'a': [], 'c': [{'privilege': 'p', 'resource': {'stream': 's', 'dataset': 'd'}}]}))
        self.assertEqual(list(data), ['a', 'b', 'c'])
        self.assertIsNone(data['b'])
        self.assertEqual(data['c'][0]['resource'], {'dataset': 'd', 'stream': 's'})

    def test_tabs_render_as_four_spaces_with_zero_width(self):
        self.assertEqual(phase5.width('a\tb'), 2)
        self.assertIn(b'a    b ', phase5.user_table([('a\tb', ['r'], None)]))


class ModelTests(unittest.TestCase):
    def model(self):
        return phase5.Model(phase5.SEED)

    def test_additive_role_patch_preserves_and_deduplicates_state(self):
        model = self.model()
        status, _, mutated = model.handle('PATCH', ['user', 'bob', 'role', 'add'], {}, b'["admin","reader","admin"]')
        self.assertEqual((status, mutated), (200, True))
        self.assertEqual(model.user('bob')['roles'], ['reader', 'admin'])
        self.assertEqual(model.user('bob')['groups'], {'ops': ['editor']})

    def test_validation_failures_do_not_mutate(self):
        for method, segments, body in [('PATCH', ['user', 'bob', 'role', 'add'], b'["ghost"]'),
                                       ('PATCH', ['user', 'ghost', 'role', 'add'], b'["admin"]'),
                                       ('POST', ['user', 'alice'], b'["admin"]'), ('POST', ['user', 'x'], b'{}'),
                                       ('DELETE', ['role', 'admin'], b''), ('DELETE', ['logstream', 'ghost'], b''),
                                       ('PUT', ['logstream', 'app'], b'')]:
            model = self.model()
            before = model.snapshot()
            status, _, mutated = model.handle(method, segments, {}, body)
            with self.subTest(method=method, segments=segments):
                self.assertFalse(mutated)
                self.assertGreaterEqual(status, 400)
                self.assertEqual(model.snapshot(), before)

    def test_dataset_headers_create_typed_state_and_info(self):
        model = self.model()
        model.handle('PUT', ['logstream', 'm'], {'x-p-telemetry-type': 'metrics', 'x-p-log-source': 'otel-metrics'}, b'')
        status, body, _ = model.handle('GET', ['logstream', 'm', 'info'], {}, b'')
        self.assertEqual((status, json.loads(body)['telemetryType']), (200, 'metrics'))
        self.assertEqual(model.datasets['m'], {'type': 'metrics', 'source': 'otel-metrics'})

    def test_user_role_detail_has_direct_and_group_maps(self):
        status, body, _ = self.model().handle('GET', ['user', 'bob', 'role'], {}, b'')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'roles': {'reader': {'actions': phase5.ROLES['reader']}},
                                            'group_roles': {'ops': {'editor': {'actions': phase5.ROLES['editor']}}}})

    def test_unknown_endpoint_is_not_answered(self):
        self.assertIsNone(self.model().handle('DELETE', ['user'], {}, b''))


class FixtureServerTests(unittest.TestCase):
    def start(self, plan):
        server = phase5.AdminServer(plan)
        server.start()
        self.addCleanup(server.close)
        host, port = server.urls['url'][len('http://'):].split(':')
        return server, host, int(port)

    def request(self, host, port, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, data

    def test_commit_ledger_and_state_control(self):
        server, host, port = self.start({'headers': {}, 'routes': [], 'state': phase5.SEED})
        self.assertEqual(self.request(host, port, 'DELETE', '/api/v1/user/bob')[0], 200)
        self.assertEqual(self.request(host, port, 'DELETE', '/api/v1/user/bob')[0], 404)
        status, data = self.request(host, port, 'GET', '/__harness/state')
        state = json.loads(data)
        self.assertEqual([u['id'] for u in state['state']['users']], ['alice'])
        self.assertEqual(state['commits'], [{'request': 0, 'op': 'DELETE /api/v1/user/bob'}])

    def test_post_commit_disconnect_commits_once(self):
        route = {'method': 'DELETE', 'path': '/api/v1/role/unused', 'model': True, 'close_before_headers': 'fin'}
        server, host, port = self.start({'headers': {}, 'routes': [route], 'state': phase5.SEED})
        with self.assertRaises((http.client.HTTPException, ConnectionError)):
            self.request(host, port, 'DELETE', '/api/v1/role/unused')
        self.assertNotIn('unused', server.model.roles)
        self.assertEqual(len(server.commits), 1)

    def test_gather_releases_concurrent_requests_together(self):
        route = {'method': 'GET', 'path_re': phase5.USER_FANOUT, 'status': 401, 'body': 'expired', 'times': 3, 'gather': 3}
        server, host, port = self.start({'headers': {}, 'routes': [route], 'state': phase5.SEED})
        results = []
        threads = [threading.Thread(target=lambda u=u: results.append(self.request(host, port, 'GET', f'/api/v1/user/{u}/role')[0]))
                   for u in ('alice', 'bob', 'alice')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(sorted(results), [401, 401, 401])
        self.assertFalse(any(e['name'] == 'gather-timeout' for e in server.events))
        self.assertEqual(self.request(host, port, 'GET', '/api/v1/user/bob/role')[0], 200)

    def test_token_rotation_validates_grant(self):
        plan = {'headers': {}, 'routes': [], 'state': phase5.SEED,
                'oauth': {'refresh_token': 'r0', 'issue': [{'access_token': 'a1', 'refresh_token': 'r1'}]}}
        server, host, port = self.start(plan)
        headers = {'Authorization': 'Bearer ' + phase5.p3.ORCHESTRATOR_TOKEN, 'Content-Type': 'application/json',
                   'Accept': 'application/json'}
        bad = json.dumps({'grant_type': 'refresh_token', 'refresh_token': 'wrong'})
        self.assertEqual(self.request(host, port, 'POST', phase5.TOKEN_PATH, bad, headers)[0], 400)
        good = json.dumps({'grant_type': 'refresh_token', 'refresh_token': 'r0'})
        status, data = self.request(host, port, 'POST', phase5.TOKEN_PATH, good, headers)
        self.assertEqual((status, json.loads(data)['access_token']), (200, 'a1'))
        self.assertEqual(self.request(host, port, 'POST', phase5.TOKEN_PATH, good, headers)[0], 400)

    def test_rejected_session_never_reaches_model(self):
        plan = {'headers': {}, 'routes': [], 'state': phase5.SEED, 'reject_sessions': ['old']}
        server, host, port = self.start(plan)
        self.assertEqual(self.request(host, port, 'DELETE', '/api/v1/user/bob', headers={'Cookie': 'session=old'})[0], 401)
        self.assertEqual(server.commits, [])


class CorpusTests(unittest.TestCase):
    def test_unique_names_and_full_inventory(self):
        cases = phase5.corpus()
        self.assertEqual(len(cases), len({c['name'] for c in cases}))
        commands = {tuple(s['argv'][:2]) for c in cases for s in c['steps']}
        for command in [('dataset', 'add'), ('dataset', 'remove'), ('dataset', 'rm'), ('user', 'add'), ('user', 'remove'),
                        ('user', 'rm'), ('user', 'set-role'), ('user', 'list'), ('user', 'ls'), ('role', 'remove'),
                        ('role', 'rm'), ('role', 'list'), ('role', 'ls')]:
            self.assertIn(command, commands)
        self.assertNotIn(('role', 'add'), commands)

    def test_interactive_paths_are_boundaries_not_cases(self):
        argv = [b['argv'] for b in phase5.deferred_boundaries()]
        self.assertIn(['role', 'add', 'newrole'], argv)
        self.assertIn(['dataset', 'add', 'new'], argv)
        for case in phase5.corpus():
            for step in case['steps']:
                if step['argv'][:2] == ['dataset', 'add']:
                    self.assertTrue(any(a.startswith('--type') for a in step['argv']), case['name'])

    def test_required_invariant_cases_present(self):
        names = {c['name'] for c in phase5.corpus()}
        for name in ('refresh-precommit-user-remove', 'refresh-replay-body-set-role', 'refresh-contention-user-list',
                     'refresh-contention-role-list', 'disconnect-after-commit-no-retry', 'devfull-after-commit-user-remove',
                     'user-add-cloud-rejected-cloud-key', 'lifecycle-dataset-traces', 'lifecycle-user', 'lifecycle-role',
                     'user-remove-stalled-success-body', 'user-add-slow-body-read-fully', 'user-add-stalled-body-deadline'):
            self.assertIn('p5-' + name, names)

    def test_mutating_cases_declare_independent_final_state(self):
        for case in phase5.corpus():
            self.assertEqual(set(case['final_state']), {'datasets', 'users', 'roles'}, case['name'])

    def test_fanout_rule_only_on_list_commands(self):
        for case in phase5.corpus():
            if case['compare'].get('fanout'):
                self.assertTrue(any(s['argv'][1] in ('list', 'ls') for s in case['steps']), case['name'])

    def test_returned_corpus_is_independent(self):
        first = phase5.corpus()
        first[0]['steps'][0]['argv'][0] = 'changed'
        self.assertEqual(phase5.corpus()[0]['steps'][0]['argv'][0], 'dataset')

    def capture(self):
        cases = phase5.corpus()
        return dict(mode='check-reference', partial=False, failed=0, tiers=['gate'],
                    corpus_sha256=phase5.run.digest(phase5.run.canonical(cases)),
                    cases=[{'name': c['name']} for c in cases if c['tier'] == 'gate'])

    def test_capture_tamper_rejected(self):
        report = self.capture()
        digest = report['corpus_sha256']
        phase5.validate_capture(report, digest, phase5.corpus())
        for change in ('digest', 'partial', 'failure', 'missing', 'duplicate', 'mode', 'tier'):
            changed = copy.deepcopy(report)
            if change == 'digest': changed['corpus_sha256'] = 'x'
            elif change == 'partial': changed['partial'] = True
            elif change == 'failure': changed['failed'] = 1
            elif change == 'missing': changed['cases'].pop()
            elif change == 'duplicate': changed['cases'].append(changed['cases'][0])
            elif change == 'mode': changed['mode'] = 'compare'
            elif change == 'tier': changed['tiers'] = ['full60s']
            with self.subTest(change=change), self.assertRaises(ValueError):
                phase5.validate_capture(changed, digest, phase5.corpus())


@unittest.skipUnless(GO.is_file(), 'pinned Go reference not built')
class GoSmokeTests(unittest.TestCase):
    def test_go_lifecycle_and_self_compare(self):
        case = next(c for c in phase5.corpus() if c['name'] == 'p5-lifecycle-user')
        with tempfile.TemporaryDirectory() as tmp:
            first = phase5.sample(GO, case, Path(tmp) / 'a', 15)
            second = phase5.sample(GO, case, Path(tmp) / 'b', 15)
        self.assertEqual(phase5.expectation_issues(case, first), [])
        self.assertEqual(phase5.differences(case, first, second), [])
        self.assertEqual(phase5.signature(case, first), phase5.signature(case, second))

    def test_mutated_candidate_state_is_detected(self):
        case = next(c for c in phase5.corpus() if c['name'] == 'p5-user-remove')
        with tempfile.TemporaryDirectory() as tmp:
            reference = phase5.sample(GO, case, Path(tmp) / 'a', 15)
        candidate = copy.deepcopy(reference)
        candidate['steps'][0]['state_after']['commits'].append({'request': 1, 'op': 'DELETE /api/v1/user/bob'})
        self.assertIn('step 0: fixture state or commit ledger differs', phase5.differences(case, reference, candidate))
        candidate = copy.deepcopy(reference)
        candidate['steps'][0]['requests'][0]['headers']['content-length'] = '0'
        self.assertTrue(any('requests differ' in i for i in phase5.differences(case, reference, candidate)))


if __name__ == '__main__':
    unittest.main()
