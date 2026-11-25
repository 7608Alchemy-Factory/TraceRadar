"""Score basic Event lifecycle across fixed replay stages without guessing semantic matches.

The script never changes frozen ground truth. Ambiguous Event matches and free-text
statuses are exported as review items; optional adjudication makes those scoreable.
"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERDICTS = {'correct', 'incorrect', 'feature_gap', 'unclear'}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def item_id(evidence):
    return evidence['external_id'].rsplit(':', 1)[-1]


def score(report, gold, adjudication=None):
    adjudication = adjudication or {}
    if report['result']['case_id'] != gold['case_id']:
        raise ValueError('Report and ground truth have different case IDs.')
    if report['experiment']['gold_sha256'] != adjudication.get('gold_sha256', report['experiment']['gold_sha256']):
        raise ValueError('Adjudication was made against another ground truth version.')
    stages = report['result']['stages']
    expected_by_stage = {row['stage_id']: row for row in gold['stage_expectations']}
    if [stage['stage_id'] for stage in stages] != list(expected_by_stage):
        raise ValueError('Report and ground truth have different stages.')
    gold_items = {row['item_id']: set(row['event_keys']) for row in gold['evidence_labels']}
    override_matches = {}
    for row in adjudication.get('event_matches', []):
        key = (row['stage_id'], row['event_key'])
        if key in override_matches:
            raise ValueError(f'Duplicate Event match: {key}')
        override_matches[key] = row['actual_event_id']
    reviews = {}
    for row in adjudication.get('status_reviews', []):
        key = (row['stage_id'], row['event_key'])
        if key in reviews or row['verdict'] not in VERDICTS:
            raise ValueError(f'Duplicate or invalid status review: {key}')
        reviews[key] = row

    rows = []
    previous_expected = {}
    valid_pairs = {(stage['stage_id'], event['event_key']) for stage in stages
                   for event in expected_by_stage[stage['stage_id']]['events']}
    unknown = (set(override_matches) | set(reviews)) - valid_pairs
    if unknown:
        raise ValueError(f'Unknown adjudication rows: {sorted(unknown)}')
    for stage in stages:
        stage_id = stage['stage_id']
        expected = expected_by_stage[stage_id]['events']
        actual = {detail['event']['event_id']: detail for detail in stage['events']}
        support = {event_id: {item_id(e) for e in detail['evidence']}
                   for event_id, detail in actual.items()}
        candidates = {}
        for event in expected:
            key = event['event_key']
            relevant_items = {item for item, keys in gold_items.items() if key in keys}
            candidates[key] = [eid for eid, items in support.items() if items & relevant_items]
        reverse = Counter(eid for ids in candidates.values() for eid in ids)
        for event in expected:
            key = event['event_key']
            pair = (stage_id, key)
            chosen = override_matches.get(pair)
            if pair in override_matches:
                if chosen is not None and chosen not in actual:
                    raise ValueError(f'Adjudicated Event ID not found at {stage_id}: {chosen}')
                match_basis = 'human'
            elif len(candidates[key]) == 1 and reverse[candidates[key][0]] == 1:
                chosen = candidates[key][0]
                match_basis = 'unique_shared_evidence'
            else:
                chosen = None
                match_basis = 'ambiguous' if candidates[key] else 'no_evidence_match'
            expected_status = event['lifecycle_status']
            actual_status = actual[chosen]['event']['state']['current_status'] if chosen else None
            prior = previous_expected.get(key)
            is_change = prior is not None and prior != expected_status
            previous_expected[key] = expected_status
            time_milestones = [m['milestone_id'] for m in gold['milestones']
                               if m['stage_id'] == stage_id and m['event_key'] == key
                               and m['trigger_type'] == 'time']
            if expected_status == 'not_applicable':
                verdict = 'not_applicable'
            elif pair in reviews:
                if not chosen and reviews[pair]['verdict'] != 'unclear':
                    raise ValueError(f'Cannot score status without an aligned Event: {pair}')
                verdict = reviews[pair]['verdict']
            elif chosen is None:
                verdict = 'alignment_needed' if candidates[key] else 'event_missing'
            elif actual_status == expected_status:
                verdict = 'correct'
            else:
                verdict = 'review_needed'
            updated_in_stage = bool(chosen and any(
                change['run_id'] == stage['run']['run']['run_id']
                for change in actual[chosen]['history']))
            rows.append({'stage_id': stage_id, 'as_of': stage['as_of'], 'event_key': key,
                         'expected_status': expected_status, 'actual_event_id': chosen,
                         'actual_status': actual_status, 'match_basis': match_basis,
                         'candidate_event_ids': candidates[key], 'verdict': verdict,
                         'expected_transition': is_change,
                         'event_updated_in_stage': updated_in_stage,
                         'time_trigger_milestone_ids': time_milestones,
                         'possible_cause': 'time_update_gap' if chosen and actual_status != expected_status
                         and is_change and time_milestones and not updated_in_stage else None,
                         'internal_offer_status_scored': False,
                         'review_note': reviews.get(pair, {}).get('note', '')})
    summary = dict(Counter(row['verdict'] for row in rows))
    transitions = [row for row in rows if row['expected_transition']]
    evaluation_valid = report.get('diagnostic', {}).get('evaluation_valid', False)
    result = {'case_id': gold['case_id'], 'annotation_status': gold['annotation_status'],
              'evaluation_valid': evaluation_valid,
              'rows': rows, 'status_counts': summary,
              'status_accuracy': (summary.get('correct', 0) /
                                  (summary.get('correct', 0) + summary.get('incorrect', 0)))
              if evaluation_valid and not (summary.get('review_needed', 0) or summary.get('alignment_needed', 0))
              and summary.get('correct', 0) + summary.get('incorrect', 0) else None,
              'transition_counts': dict(Counter(row['verdict'] for row in transitions)),
              'scope': 'Whole-Event lifecycle only. Internal offer_status is not scored.',
              'limitations': ['Free-text status meaning and ambiguous Event boundaries need human adjudication.',
                              'A time-trigger mismatch is only a possible feature gap until reviewed.',
                              'Missing Events are counted separately from status errors.']}
    return result


def review_template(result, gold_hash):
    return {'case_id': result['case_id'], 'gold_sha256': gold_hash,
            'event_matches': [dict(stage_id=row['stage_id'], event_key=row['event_key'],
                                   actual_event_id=None, candidate_event_ids=row['candidate_event_ids'])
                              for row in result['rows'] if row['verdict'] in ('alignment_needed', 'event_missing')],
            'status_reviews': [dict(stage_id=row['stage_id'], event_key=row['event_key'],
                                    verdict='unclear', note='', expected_status=row['expected_status'],
                                    actual_status=row['actual_status'], possible_cause=row['possible_cause'])
                               for row in result['rows'] if row['verdict'] == 'review_needed']}


def markdown(result):
    lines = [f"# {result['case_id']} — Event lifecycle score", '',
             f"Ground truth: `{result['annotation_status']}`. Formal evaluation valid: `{result['evaluation_valid']}`. "
             'Whole-Event lifecycle only; internal offer_status is excluded.', '',
             f"Counts: `{json.dumps(result['status_counts'], ensure_ascii=False)}`. "
             f"Accuracy over adjudicated correct/incorrect rows: `{result['status_accuracy']}`.",
             f"Expected status transitions: `{json.dumps(result['transition_counts'], ensure_ascii=False)}`.", '',
             '| Stage | Event | Expected | Actual | Result | Possible cause |',
             '|---|---|---|---|---|---|']
    for row in result['rows']:
        lines.append('| ' + ' | '.join(str(value or '—').replace('|', '\\|') for value in
                     (row['stage_id'], row['event_key'], row['expected_status'],
                      row['actual_status'], row['verdict'], row['possible_cause'])) + ' |')
    lines += ['', 'A time-update gap is a review candidate, not an automatic finding. '
              'Unresolved rows stay out of the accuracy denominator.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--adjudication', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = read_json(args.report)
    case_id = report['result']['case_id']
    gold_path = ROOT / 'evaluation/cases' / case_id / 'ground_truth.json'
    if digest(gold_path) != report['experiment']['gold_sha256']:
        raise ValueError('Ground truth changed since this run; use the matching frozen version.')
    result = score(report, read_json(gold_path), read_json(args.adjudication) if args.adjudication else None)
    output = args.output or args.report.with_name('state_score.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    output.with_suffix('.md').write_text(markdown(result), encoding='utf-8')
    print(output.resolve())


if __name__ == '__main__':
    main()
