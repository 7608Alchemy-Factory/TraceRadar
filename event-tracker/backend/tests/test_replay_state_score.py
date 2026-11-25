import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('score_replay_state', ROOT / 'scripts/score_replay_state.py')
SCORER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCORER)


def test_mcd_whole_event_expiry_is_separate_from_offer_status():
    gold = json.loads((ROOT / 'evaluation/cases/mcd_hk_campaigns_v1/ground_truth.json').read_text(encoding='utf-8'))
    gold['stage_expectations'] = gold['stage_expectations'][:2]
    weekly_id = 'weekly-actual'

    def stage(index, weekly_status, run_id):
        event = {'event': {'event_id': weekly_id,
                           'state': {'current_status': weekly_status}},
                 'evidence': [{'external_id': 'MCD-01'}],
                 'history': [{'run_id': 'first-run'}]}
        return {'stage_id': gold['stage_expectations'][index]['stage_id'],
                'as_of': '2026-09-16T15:59:59Z',
                'run': {'run': {'run_id': run_id}}, 'events': [event]}

    report = {'result': {'case_id': gold['case_id'],
                         'stages': [stage(0, 'active', 'first-run'), stage(1, 'active', 'second-run')]},
              'experiment': {'gold_sha256': 'test-hash'},
              'diagnostic': {'evaluation_valid': True}}
    result = SCORER.score(report, gold)
    weekly = [row for row in result['rows'] if row['event_key'] == 'weekly_rewards']
    assert weekly[0]['verdict'] == 'correct'
    assert weekly[1]['verdict'] == 'review_needed'
    assert weekly[1]['possible_cause'] == 'time_update_gap'
    assert weekly[1]['expected_transition'] is True
    assert weekly[1]['internal_offer_status_scored'] is False
    assert result['status_accuracy'] is None  # do not publish accuracy with unresolved expiry

    reviewed = SCORER.score(report, gold, {'gold_sha256': 'test-hash',
        'status_reviews': [{'stage_id': 's2_trial', 'event_key': 'weekly_rewards',
                            'verdict': 'feature_gap', 'note': 'No time-driven update.'}]})
    assert reviewed['status_counts']['feature_gap'] == 1
    assert reviewed['transition_counts']['feature_gap'] == 1


def test_mcd_shared_article_does_not_force_event_alignment():
    gold = json.loads((ROOT / 'evaluation/cases/mcd_hk_campaigns_v1/ground_truth.json').read_text(encoding='utf-8'))
    gold['stage_expectations'] = [gold['stage_expectations'][2]]
    actual = [{'event': {'event_id': 'actual-1', 'state': {'current_status': 'active'}},
               'evidence': [{'external_id': 'MCD-03'}], 'history': []},
              {'event': {'event_id': 'actual-2', 'state': {'current_status': 'active'}},
               'evidence': [{'external_id': 'MCD-03'}], 'history': []}]
    report = {'result': {'case_id': gold['case_id'], 'stages': [
        {'stage_id': 's3_vouchers', 'as_of': '2026-09-28T15:59:59Z',
         'run': {'run': {'run_id': 'third-run'}}, 'events': actual}]},
        'experiment': {'gold_sha256': 'test-hash'},
        'diagnostic': {'evaluation_valid': True}}
    result = SCORER.score(report, gold)
    for row in result['rows']:
        if row['event_key'] in ('prepaid_vouchers', 'tea_offer'):
            assert row['verdict'] == 'alignment_needed'
    reviewed = SCORER.score(report, gold, {'event_matches': [
        {'stage_id': 's3_vouchers', 'event_key': 'tea_offer', 'actual_event_id': 'actual-2'}]})
    tea = next(row for row in reviewed['rows'] if row['event_key'] == 'tea_offer')
    assert tea['match_basis'] == 'human'
    assert tea['verdict'] == 'correct'
