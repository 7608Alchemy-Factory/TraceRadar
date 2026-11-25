"""Score adjudicated live RSS candidates; leave semantic Event claims for human review."""
import argparse
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def normalized_url(raw):
    try:
        parts = urlsplit(raw)
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                           parts.path.rstrip('/') or '/', parts.query, ''))
    except ValueError:
        return raw


def candidate_key(row):
    evidence = row['evidence']
    return f"{evidence['source_id']}:{evidence['external_id']}"


def score(capture, labels):
    manifest = capture['manifest']
    if (labels['case_id'], labels['capture_id']) != (manifest['case_id'], manifest['capture_id']):
        raise ValueError('Label file belongs to a different capture.')
    raw_rows = capture['raw_inputs']
    keys = {candidate_key(row) for row in raw_rows}
    labels_by_key = {}
    for label in labels['candidate_labels']:
        key = label['candidate_key']
        if key not in keys or key in labels_by_key:
            raise ValueError(f'Unknown or duplicate candidate label: {key}')
        if label['relevance'] not in ('relevant', 'irrelevant', 'uncertain', 'unjudged'):
            raise ValueError(f'Invalid relevance label for {key}')
        labels_by_key[key] = label
    start = datetime.fromisoformat(manifest['window_start'])
    end = datetime.fromisoformat(manifest['window_end'])
    decisions = {row['evidence']['evidence_id']: row for row in capture['evidence_decisions']}
    rows = []
    counts = dict(tp=0, fp=0, fn=0, tn=0, unjudged=0, uncertain=0,
                  no_decision=0, before_window=0, after_window=0)
    for raw in raw_rows:
        evidence, outcome = raw['evidence'], raw.get('outcome') or {}
        published = datetime.fromisoformat(evidence['published_at'].replace('Z', '+00:00'))
        in_window = start <= published <= end
        if published < start:
            counts['before_window'] += 1
        elif published > end:
            counts['after_window'] += 1
        key = candidate_key(raw)
        label = labels_by_key.get(key, {}).get('relevance', 'unjudged')
        assessment = (decisions.get(evidence['evidence_id']) or {}).get('assessment') or {}
        selected = assessment.get('is_relevant')
        if selected is None and outcome.get('status') == 'irrelevant':
            selected = False  # deterministic prefilter may reject before model assessment
        record = {'candidate_key': key, 'source_id': evidence['source_id'],
                  'url': evidence['url'], 'title': evidence['title'],
                  'published_at': evidence['published_at'], 'in_window': in_window,
                  'label': label, 'selected': selected, 'processing_status': outcome.get('status')}
        rows.append(record)
        if not in_window:
            continue
        if label == 'unjudged':
            counts['unjudged'] += 1
        elif label == 'uncertain':
            counts['uncertain'] += 1
        elif selected is None:
            counts['no_decision'] += 1
        else:
            counts[('tp' if label == 'relevant' else 'fp') if selected else
                   ('fn' if label == 'relevant' else 'tn')] += 1
    tp, fp, fn = counts['tp'], counts['fp'], counts['fn']
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall) if precision + recall else 0.0) \
        if precision is not None and recall is not None else None

    window_rows = [row for row in rows if row['in_window']]
    retrieved_urls = {normalized_url(row['url']) for row in window_rows}
    retrieved_keys = {row['candidate_key'] for row in window_rows}
    known = []
    for item in labels.get('known_items', []):
        urls = item.get('acceptable_urls', [])
        matched = bool(retrieved_keys.intersection(item.get('matched_candidate_keys', [])) or
                       retrieved_urls.intersection(normalized_url(url) for url in urls))
        known.append({'id': item['id'], 'retrieved': matched,
                      'matched_by': 'URL or human-entered candidate key' if matched else None})
    known_coverage = sum(item['retrieved'] for item in known) / len(known) if known else None

    approved_ids = {source['source_id'] for source in capture['approved_sources']}
    evidence_by_id = {row['evidence']['evidence_id']: row['evidence'] for row in raw_rows}
    grounding = dict(fact_refs=0, missing_refs=0, unapproved_source_refs=0, future_refs=0)
    for detail in capture['events']:
        for fact in detail['event']['state']['key_facts']:
            for ref in fact['evidence_ids']:
                grounding['fact_refs'] += 1
                evidence = evidence_by_id.get(ref)
                if evidence is None:
                    grounding['missing_refs'] += 1
                else:
                    grounding['unapproved_source_refs'] += evidence['source_id'] not in approved_ids
                    grounding['future_refs'] += datetime.fromisoformat(evidence['published_at'].replace('Z', '+00:00')) > end
    steps = capture['run_detail']['steps']
    fetches = [step for step in steps if step['step_name'] == 'fetch_source']
    trace = {'sequence_ordered': [step['sequence'] for step in steps] == list(range(1, len(steps) + 1)),
             'fetch_source_steps': len(fetches),
             'failed_fetches': [step['error'] for step in fetches if step['status'] == 'failed'],
             'fetch_stats': [step['decision'] for step in fetches]}
    result = {'case_id': manifest['case_id'], 'capture_id': manifest['capture_id'],
              'run_status': capture['run_detail']['run']['status'],
              'information_relevance': {'scope': 'adjudicated fetched candidates with an observable decision inside the analysis window',
                                        'counts': counts, 'precision': precision, 'recall_in_judged_pool': recall, 'f1_in_judged_pool': f1},
              'known_item_coverage': {'scope': 'prelisted known items only; not web recall',
                                      'items': known, 'coverage': known_coverage},
              'retrieval_diagnostics': {'fetched_total': len(raw_rows), 'in_window': len(window_rows),
                                        'unique_urls_in_window': len(retrieved_urls),
                                        'duplicate_url_count_in_window': len(window_rows) - len(retrieved_urls),
                                        'approved_source_count': len(approved_ids)},
              'grounding_structure': grounding, 'trace_structure': trace,
              'content_scores': {'topic_evolution': None, 'state_update': None,
                                 'notification_quality': None, 'semantic_grounding': None,
                                 'reason': 'Requires human Event/milestone matching and claim support judgments.'},
              'candidate_rows': rows}
    return result


def markdown(result):
    rel = result['information_relevance']
    counts = rel['counts']
    pct = lambda value: 'n/a' if value is None else f'{value:.3f}'
    return '\n'.join([
        f"# Live RSS evaluation — {result['case_id']} / {result['capture_id']}", '',
        f"Run status: `{result['run_status']}`. Fetched: {result['retrieval_diagnostics']['fetched_total']}; "
        f"inside analysis window: {result['retrieval_diagnostics']['in_window']}.", '',
        '## Information relevance (judged fetched pool only)', '',
        f"TP={counts['tp']}, FP={counts['fp']}, FN={counts['fn']}, TN={counts['tn']}; "
        f"unjudged={counts['unjudged']}, uncertain={counts['uncertain']}, no decision={counts['no_decision']}.",
        f"Precision={pct(rel['precision'])}; recall in judged pool={pct(rel['recall_in_judged_pool'])}; "
        f"F1={pct(rel['f1_in_judged_pool'])}.", '',
        f"Known-item coverage: {pct(result['known_item_coverage']['coverage'])} "
        '(not whole-web recall).', '',
        '## Structural checks', '',
        f"Fact references: {result['grounding_structure']['fact_refs']}; "
        f"missing={result['grounding_structure']['missing_refs']}; "
        f"unapproved source={result['grounding_structure']['unapproved_source_refs']}; "
        f"future={result['grounding_structure']['future_refs']}.",
        f"Trace sequence ordered: {result['trace_structure']['sequence_ordered']}; "
        f"failed source fetches: {len(result['trace_structure']['failed_fetches'])}.", '',
        'Event boundaries, state meaning, notifications and semantic evidence support remain pending human adjudication.',
        'The RSS connector sees feed summaries and does not fetch article bodies. The runtime enforces only the window end; earlier feed items may still be processed.', ''
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--labels', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = score(read_json(args.capture), read_json(args.labels))
    output = args.output or args.capture.with_name('score.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, result)
    output.with_suffix('.md').write_text(markdown(result), encoding='utf-8')
    print(output.resolve())


if __name__ == '__main__':
    main()
