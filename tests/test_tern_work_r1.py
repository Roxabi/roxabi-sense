import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from roxabi_sense.collectors.tern.metadata import opaque
from roxabi_sense.report.dwell.repos import RepoAttribution, attribute_repos
from roxabi_sense.report.dwell.tern_work import add_tern_work, summarize_tern_work
from roxabi_sense.report.segments import FocusSegment
from roxabi_sense.store import Event

START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(seconds=60)


def ts(seconds):
    return (START + timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')


def facts(panes):
    snapshot = Event(1, ts(0), 'tern_snapshot', {
        'run_ref': 'run', 'state': 'observed', 'closed': [],
        'windows': [{'panes': panes}, {'panes': panes}],
    })
    scopes = {ref: {
        'proof_ref': ref, 'semantic_ref': 's',
        'started_at': ts(0), 'last_observed_at': ts(60), 'fresh_until': ts(70),
    } for p in panes for ref in (p['pane_work_proof_ref'], p['process_proof_ref'])}
    return [snapshot], json.dumps({'run_ref': 'run', 'snapshot_id': 1,
                                  'healthy': True, 'scopes': scopes})


def pane(repo, proof, path='/projects/demo'):
    return {'pane_ref': 'pane-' + proof, 'repo_ref': repo, 'cwd': path,
            'pane_work_proof_ref': 'pane-' + proof, 'process_proof_ref': 'process-' + proof,
            'pane_working': True, 'omp_foreground': True}


def coverage(second, state):
    return Event(10 + second, ts(second), 'focus_coverage', {'state': state})


def test_attention_requires_observed_coverage_and_partitions_it():
    p = pane('private-repo', 'proof')
    snapshots, meta = facts([p])
    editor = FocusSegment(ts(0), ts(60), 60, 'editor', 'Editor')
    cov = [coverage(0, 'legacy_unverified'), coverage(20, 'observed'),
           coverage(40, 'unknown')]
    result = summarize_tern_work(snapshots, meta, [editor], START, END, coverage=cov)
    assert (result.omp_working_s, result.attended_s, result.unattended_s,
            result.unknown_attention_s) == (60, 0, 20, 40)
    repos = add_tern_work(RepoAttribution([], [], None), snapshots, meta, [editor],
                          START, END, coverage=cov)
    assert (repos.agents[0].working_s, repos.agents[0].unfocused_s) == (60, 20)
    assert summarize_tern_work(snapshots, meta, [editor], START, END).unknown_attention_s == 60


def test_context_matching_and_unknown_tern_context():
    p = pane('repo', 'proof')
    snapshots, meta = facts([p])
    ref = opaque('context', p['pane_ref'], None, p['repo_ref'])
    segments = [FocusSegment(ts(0), ts(30), 30, 'so.stencil.tern', 'Tern', context_ref=ref),
                FocusSegment(ts(30), ts(60), 30, 'so.stencil.tern', 'Tern')]
    cov = [coverage(0, 'observed')]
    result = summarize_tern_work(snapshots, meta, segments, START, END, coverage=cov)
    assert (result.attended_s, result.unattended_s, result.unknown_attention_s) == (30, 0, 30)
    repos = add_tern_work(RepoAttribution([], [], None), snapshots, meta, segments,
                          START, END, coverage=cov)
    assert repos.agents[0].unfocused_s == 0


def test_source_scoped_repositories_remain_separate_without_public_identity():
    a = pane('secret-remote-a', 'a', '/a/demo')
    b = pane('secret-remote-b', 'b', '/b/demo')
    snapshots, meta = facts([a, b])
    result = add_tern_work(RepoAttribution([], [], None), snapshots, meta, [], START, END)
    assert [(row.repo, row.working_s) for row in result.agents] == [('demo', 60), ('demo', 60)]
    segments = [FocusSegment(ts(0), ts(30), 30, 'so.stencil.tern', 'Tern',
                             cwd=a['cwd'], context_ref='ctx-a', repo_ref=a['repo_ref']),
                FocusSegment(ts(30), ts(60), 30, 'so.stencil.tern', 'Tern',
                             cwd=b['cwd'], context_ref='ctx-b', repo_ref=b['repo_ref'])]
    focus = attribute_repos([], segments, window_start=START, horizon=END)
    assert focus.focus == [('demo', 30), ('demo', 30)]
    public = json.dumps(asdict(result)) + json.dumps(asdict(focus))
    assert 'secret-remote' not in public and '/a/demo' not in public and '/b/demo' not in public


def test_independent_proofs_preserve_pane_work_through_process_failure():
    p = pane('repo', 'proof')
    snapshots, meta = facts([p])
    failed = dict(p, omp_foreground=None, process_proof_ref=None)
    snapshots.append(Event(2, ts(20), 'tern_snapshot', {
        'run_ref': 'run', 'state': 'observed', 'windows': [{'panes': [failed]}],
        'closed': [{'proof_ref': p['process_proof_ref'], 'started_at': ts(0),
                    'confirmed_through': ts(15)}],
    }))
    meta_obj = json.loads(meta)
    meta_obj['snapshot_id'] = 2
    del meta_obj['scopes'][p['process_proof_ref']]
    result = summarize_tern_work(snapshots, json.dumps(meta_obj), [], START, END)
    assert (result.pane_working_s, result.omp_working_s, result.unknown_attention_s) == (
        60, 15, 15
    )
    repos = add_tern_work(RepoAttribution([], [], None), snapshots, json.dumps(meta_obj),
                          [], START, END)
    assert repos.agents[0].working_s == 15


def test_fractional_first_point_and_closed_proofs_across_restart():
    p = pane('repo', 'proof')
    snapshots, meta = facts([p])
    data = json.loads(meta)
    for scope in data['scopes'].values():
        scope['started_at'] = scope['last_observed_at'] = ts(0.8)
    meta = json.dumps(data)
    first = summarize_tern_work(snapshots, meta, [], START, END)
    assert first.pane_working_s == first.omp_working_s == 0
    assert not add_tern_work(RepoAttribution([], [], None), snapshots, meta, [],
                             START, END).agents
    closed = [{'proof_ref': ref, 'started_at': ts(0.8), 'confirmed_through': ts(10.3)}
              for ref in (p['pane_work_proof_ref'], p['process_proof_ref'])]
    snapshots.append(Event(2, ts(11), 'tern_snapshot', {
        'run_ref': 'new-run', 'state': 'unknown', 'closed': closed,
    }))
    result = summarize_tern_work(snapshots, None, [], START, END)
    assert (result.pane_working_s, result.omp_working_s) == (9.5, 9.5)
    repos = add_tern_work(RepoAttribution([], [], None), snapshots, None, [], START, END)
    assert repos.agents[0].working_s == 9.5
    clipped = summarize_tern_work(snapshots, None, [], START + timedelta(seconds=1.3),
                                  START + timedelta(seconds=8.3))
    assert (clipped.pane_working_s, clipped.omp_working_s) == (7, 7)


def test_omp_work_intersects_distinct_pane_and_process_intervals():
    p = pane('repo', 'proof')
    snapshots, meta = facts([p])
    data = json.loads(meta)
    data['scopes'][p['pane_work_proof_ref']]['started_at'] = ts(5.2)
    data['scopes'][p['pane_work_proof_ref']]['last_observed_at'] = ts(50.2)
    data['scopes'][p['process_proof_ref']]['started_at'] = ts(10.7)
    data['scopes'][p['process_proof_ref']]['last_observed_at'] = ts(40.7)
    meta = json.dumps(data)
    result = summarize_tern_work(snapshots, meta, [], START, END)
    assert (result.pane_working_s, result.omp_working_s, result.unknown_attention_s) == (
        45, 30, 30
    )
    repos = add_tern_work(RepoAttribution([], [], None), snapshots, meta, [], START, END)
    assert repos.agents[0].working_s == 30


def test_rounded_closure_snapshot_preserves_precise_confirmed_end():
    p = pane('repo', 'proof')
    snapshots, _ = facts([p])
    closed = [{'proof_ref': ref, 'started_at': ts(0.8), 'confirmed_through': ts(5.1)}
              for ref in (p['pane_work_proof_ref'], p['process_proof_ref'])]
    snapshots.append(Event(2, ts(5), 'tern_snapshot', {
        'run_ref': 'run', 'state': 'unknown', 'closed': closed,
    }))
    work = summarize_tern_work(snapshots, None, [], START, END)
    assert work.pane_working_s == work.omp_working_s == 4.3
    repos = add_tern_work(RepoAttribution([], [], None), snapshots, None, [], START, END)
    assert repos.agents[0].working_s == 4.3
    precise = Event(2, ts(5), 'tern_snapshot', {
        'run_ref': 'run', 'state': 'unknown', 'closed': closed, '_published_at': ts(5.9),
    })
    precise_work = summarize_tern_work([snapshots[0], precise], None, [], START, END)
    assert precise_work.pane_working_s == precise_work.omp_working_s == 4.3
    precise_repos = add_tern_work(RepoAttribution([], [], None), [snapshots[0], precise],
                                  None, [], START, END)
    assert precise_repos.agents[0].working_s == 4.3
