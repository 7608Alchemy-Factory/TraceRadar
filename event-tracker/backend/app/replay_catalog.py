"""Only runtime input corpora, never annotation files or expected results."""
import json
from .core import AppError


def replay_catalog(settings):
    catalog=[]
    for identity,name,path in [('lbj','LeBron James 转会追踪','lbj_2026_extended/all.json'),
                               ('nba','NBA 多事件追踪','nba_2026_multi_event/all.json')]:
        full = settings.corpus_root / path
        if not full.exists():
            continue
        try:
            rows=json.loads(full.read_text(encoding='utf-8'))
            dates=sorted({r['published_at'] for r in rows})
        except (ValueError,KeyError,TypeError):
            raise AppError('INVALID_CORPUS', 'A bundled corpus could not be read.', 422)
        cutoffs=list(dict.fromkeys(dates[min(len(dates)-1, int(len(dates)*n/3)-1)] for n in (1,2,3))) if dates else []
        catalog.append(dict(corpus_id=identity,name=name,corpus_path=path,records=len(rows),cutoffs=cutoffs,
                            provenance='队友提供的离线测试语料，新闻真实性与标签仍需核验'))
    return catalog
