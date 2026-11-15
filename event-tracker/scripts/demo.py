"""Run the fixed Mock demo against a running backend; no paid calls."""
import json
import time
from pathlib import Path
import httpx


def main():
    with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10) as client:
        def request(method, path, **kwargs):
            response = client.request(method, '/api' + path, **kwargs)
            response.raise_for_status()
            return response.json()
        topic = request('POST', '/topics', json={'title':'ExampleSoft security demo', 'description':'Track ExampleSoft vulnerabilities, versions and patches.'})
        identity = topic['topic_id']
        request('PUT', f'/topics/{identity}/sources', json={'approved_source_ids':['vendor','research']})
        for batch in ('1','2','3','1','2','3'):
            run = request('POST', f'/topics/{identity}/runs',json={'fixture_batch_id':batch,'provider':'mock'})
            for _ in range(300):
                detail = request('GET',f"/runs/{run['run_id']}")
                if detail['run']['status'] not in ('pending','running'):
                    break
                time.sleep(.1)
            assert detail['run']['status']=='completed', detail
        dashboard = request('GET', f'/topics/{identity}/dashboard')
        details = [request('GET', f"/events/{e['event_id']}") for e in dashboard['events']]
        result = {'dashboard':dashboard,'events':details,'notifications':request('GET',f'/topics/{identity}/notifications')}
        expected = json.loads((Path(__file__).resolve().parents[1]/'evaluation/expected/demo.json').read_text())
        assert len(details)==expected['events']
        assert sum(len(e['history']) for e in details)==expected['transitions']
        assert dashboard['notification_counts']==expected['notifications']
        output = Path(__file__).resolve().parents[1]/'evaluation/reports/mock-demo.json'
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f'PASS: 2 events, 4 transitions, 4 notifications; replay unchanged. Export: {output}')


if __name__=='__main__': main()
