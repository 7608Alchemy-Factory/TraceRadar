"""Export runtime outputs and a separate, blank human-annotation template. No model calls."""
import argparse
import json
from pathlib import Path
import httpx


def export_case(client, topic_id, output):
    def get(path):
        response=client.get('/api'+path)
        response.raise_for_status()
        return response.json()
    dashboard=get(f'/topics/{topic_id}/dashboard')
    evidence=get(f'/topics/{topic_id}/evidence')
    runs=[get('/runs/'+r['run_id']) for r in dashboard['recent_runs']]
    case=dict(case_id=topic_id,dashboard=dashboard,evidence=evidence,runs=runs,
              events=[get('/events/'+e['event_id']) for e in dashboard['events']],
              notifications=get(f'/topics/{topic_id}/notifications'))
    output.mkdir(parents=True,exist_ok=True)
    (output/'agent-output.json').write_text(json.dumps(case,ensure_ascii=False,indent=2),encoding='utf-8')
    template=output/'annotations.jsonl'
    if not template.exists():
        rows=[dict(case_id=topic_id,evidence_id=e['evidence']['evidence_id'],external_id=e['evidence']['external_id'],
                   title=e['evidence']['title'],published_at=e['evidence']['published_at'],content=e['evidence']['content'],
                   labels=dict(is_relevant=None,event_key=None,update_kind=None,notification_action=None,
                               supporting_evidence_ids=[],note='')) for e in evidence]
        template.write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows)+'\n',encoding='utf-8')
    (output/'README.md').write_text('# Pilot annotation\n\nagent-output.json contains actual runtime decisions. '
        'annotations.jsonl contains blank labels for human review; existing annotations are never overwritten. '
        'A blank label is unannotated, not a negative label. The runtime never reads these files. '
        'No accuracy score is computed without labels. Re-export to a new directory if the evidence set changes.\n',encoding='utf-8')
    return dict(topic_id=topic_id,evidence=len(evidence),runs=len(runs),output=str(output.resolve()))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--topic-id',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--api',default='http://127.0.0.1:8000')
    args=parser.parse_args()
    with httpx.Client(base_url=args.api,timeout=15,trust_env=False) as client:
        print(json.dumps(export_case(client,args.topic_id,args.output),ensure_ascii=False))


if __name__=='__main__':
    main()
