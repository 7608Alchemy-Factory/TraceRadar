"""Score an adjudicated search-only capture without touching Event state."""
import argparse
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def score(capture, annotations):
    if capture['case_id'] != annotations['case_id']:
        raise ValueError('Capture and annotation case IDs differ.')
    indexed = {}
    for arm, queries in capture['arms'].items():
        for query_index, query in enumerate(queries, 1):
            for item_index, item in enumerate(query['items'], 1):
                indexed[(arm, query_index, item_index)] = item
    seen = set()
    by_arm = {arm: [] for arm in capture['arms']}
    for label in annotations['labels']:
        key = (label['arm'], label['query_index'], label['item_index'])
        if key in seen or key not in indexed:
            raise ValueError(f'Duplicate or unknown annotation: {key}')
        seen.add(key)
        if label['label'] not in ('relevant', 'irrelevant', 'uncertain'):
            raise ValueError(f'Invalid relevance label: {key}')
        item = indexed[key]
        if (label['title'], label['url']) != (item['title'], item['url']):
            raise ValueError(f'Annotation does not match captured item: {key}')
        by_arm[key[0]].append((label, item))
    if seen != set(indexed):
        raise ValueError(f'{len(indexed) - len(seen)} candidates remain unannotated.')
    results = {}
    for arm, pairs in by_arm.items():
        relevant = [row for row, _ in pairs if row['label'] == 'relevant']
        irrelevant = [row for row, _ in pairs if row['label'] == 'irrelevant']
        uncertain = [row for row, _ in pairs if row['label'] == 'uncertain']
        urls = [item['url'] for _, item in pairs]
        fetches = [query['stats'] for query in capture['arms'][arm]]
        results[arm] = {
            'queries': len(capture['arms'][arm]),
            'returned': len(pairs), 'relevant': len(relevant),
            'irrelevant': len(irrelevant), 'uncertain': len(uncertain),
            'candidate_pool_precision': len(relevant) / (len(relevant) + len(irrelevant))
            if relevant or irrelevant else None,
            'known_development_ids': sorted({row['known_development_id'] for row in relevant
                                             if row['known_development_id']}),
            'unique_urls': len(set(urls)),
            'fulltext_fetched': sum(int(stats.get('fulltext_fetched', 0)) for stats in fetches),
            'fulltext_attempted': sum(int(stats.get('fulltext_attempted', 0)) for stats in fetches),
            'content_under_180_chars': sum(len(item['content']) < 180 for _, item in pairs),
        }
    human_review = annotations.get('human_review')
    return {'case_id': capture['case_id'], 'reviewer': annotations['reviewer'],
            'human_review': human_review,
            'rubric': annotations['rubric'], 'results': results,
            'limits': ['Candidate pool precision is not Precision@K: connector sorts by publication date.',
                       'Known-development coverage is not whole-web recall.',
                       'Most Google News redirect entries supplied only headlines.',
                       'The evaluation author reviewed and accepted the initial labels.'
                       if human_review else 'No human review is recorded for the AI-assisted labels.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--labels', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = score(read(args.capture), read(args.labels))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(args.output.resolve())


if __name__ == '__main__':
    main()
