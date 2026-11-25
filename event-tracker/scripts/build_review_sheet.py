"""Build a content review sheet from an existing benchmark report; makes no model calls."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def short_id(value):
    return str(value)[:8] if value else '?'


def build(report_path):
    report = read_json(report_path)
    case_id = report['result']['case_id']
    gold_path = ROOT / 'evaluation/cases' / case_id / 'ground_truth.json'
    gold = read_json(gold_path)
    gold_hash = hashlib.sha256(gold_path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    recorded_hash = report['experiment']['gold_sha256']
    claims = {claim['claim_id']: claim for claim in gold['claims']}
    stages = report['result']['stages']
    lines = [f'# {case_id}: content review sheet', '',
             f'- Run report: `{report_path.resolve()}`',
             f'- Gold status: `{gold["annotation_status"]}`',
             f'- Gold hash matches run: `{gold_hash == recorded_hash}`']
    if gold_hash != recorded_hash:
        lines.append('- **Version warning:** current gold differs from the copy hashed during the run; check the diff before treating this as a formal score.')
    lines += ['', 'Counts below are diagnostics. Event identity, claim meaning, temporal state and notification quality require the checks listed for each stage.', '']
    prior_notifications = set()
    for stage in stages:
        stage_id = stage['stage_id']
        expected = next(row for row in gold['stage_expectations'] if row['stage_id'] == stage_id)
        evidence_by_id = {row['evidence']['evidence_id']: row['evidence']['external_id'].rsplit(':', 1)[-1]
                          for row in stage['evidence']}
        actual_events = stage['events']
        current_notifications = {row['notification_id'] for row in stage['notifications']}
        new_notifications = [row for row in stage['notifications'] if row['notification_id'] not in prior_notifications]
        prior_notifications = current_notifications
        lines += [f'## {stage_id} — {stage["as_of"]}', '',
                  f'Expected events: **{len(expected["events"])}**; actual events: **{len(actual_events)}**. '
                  f'New notifications: **{len(new_notifications)}**.', '']
        for gold_event in expected['events']:
            key = gold_event['event_key']
            supported = {item for label in gold['evidence_labels'] if key in label['event_keys']
                         for item in [label['item_id']]}
            candidates = []
            for record in actual_events:
                item_ids = {evidence_by_id.get(row['evidence_id']) for row in record['evidence']}
                if supported & item_ids:
                    candidates.append(record)
            lines += [f'### Expected Event: `{key}`', '',
                      f'- Boundary: {next(row["boundary"] for row in gold["events"] if row["event_key"] == key)}',
                      f'- Expected lifecycle: `{gold_event["lifecycle_status"]}`; facets: `{json.dumps(gold_event.get("state_facets", {}), ensure_ascii=False)}`',
                      f'- Candidate actual Events by shared evidence: **{len(candidates)}** (candidates are not an automatic match).']
            for claim_id in gold_event['required_claim_ids']:
                lines.append(f'- [ ] Claim `{claim_id}`: {claims[claim_id]["statement"]}')
            for assertion in gold_event.get('forbidden_assertions', []):
                lines.append(f'- [ ] Forbidden assertion absent: {assertion}')
            lines.append('- [ ] Event boundary and current state are correct at this stage.')
            for record in candidates:
                event = record['event']
                state = event['state']
                lines += ['', f'**Actual candidate `{short_id(event["event_id"])}` — {event["title"]} (v{event["version"]})**', '',
                          f'- Status: {state["current_status"]}', '- Facts:']
                for fact in state['key_facts']:
                    refs = [evidence_by_id.get(ref, f'UNKNOWN:{short_id(ref)}') for ref in fact['evidence_ids']]
                    lines.append(f'  - {fact["fact"]} `[evidence: {", ".join(refs) or "NONE"}]`')
                invalid_refs = [ref for fact in state['key_facts'] for ref in fact['evidence_ids']
                                if ref not in evidence_by_id]
                lines.append(f'- Citation structure: **{len(invalid_refs)}** fact references missing from this stage’s ingested evidence.')
            lines.append('')
        expected_milestones = [row for row in gold['milestones'] if row['stage_id'] == stage_id]
        lines += ['### Notifications and processing', '']
        for milestone in expected_milestones:
            lines.append(f'- [ ] `{milestone["milestone_id"]}` ({milestone["trigger_type"]}): expected `{milestone["expected_action"]}`; source items {", ".join(milestone["supporting_item_ids"])}')
        for row in new_notifications:
            lines.append(f'- Actual: `{row["action"]}` / `{row["priority"]}` / Event `{short_id(row["event_id"])}` / transition `{short_id(row["transition_id"])}`')
        summary = next(row for row in report['diagnostic']['stage_summary'] if row['stage_id'] == stage_id)
        for item in summary['items']:
            if item['status'] != 'skipped':
                lines.append(f'- Input `{item["item_id"]}`: `{item["status"]}`, attempts {item["attempts"]}')
        lines.append('')
    lines += ['## Human adjudication', '',
              '- [ ] Map each actual Event to at most one expected Event; note missing, extra, merged and split Events.',
              '- [ ] Mark each required claim supported, missing, contradicted or unclear, and check its source and stage visibility.',
              '- [ ] Check lifecycle/status and prohibited assertions at every stage, including time-only changes.',
              '- [ ] Match notifications to milestones and check action, priority, duplicates and missing alerts.',
              '- [ ] Record reviewer, rubric version, disagreements and final decisions separately from the Agent run.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or args.report.with_name('review.md')
    output.write_text(build(args.report), encoding='utf-8')
    print(output.resolve())


if __name__ == '__main__':
    main()
