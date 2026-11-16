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
    scopes = {p['work_proof_ref']: {
        'proof_ref': p['work_proof_ref'], 'semantic_ref': 's',
        'started_at': ts(0), 'last_observed_at': ts(60), 'fresh_until': ts(70),
    } for p in panes}
    return [snapshot], json.dumps({'run_ref': 'run', 'snapshot_id': 1,
                                  'healthy': True, 'scopes': scopes})


def pane(repo, proof, path='/projects/demo'):
    return {'pane_ref': 'pane-' + proof, 'repo_ref': repo, 'cwd': path,
            'work_proof_ref': proof, 'pane_working': True, 'omp_working': True}


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
