"""Only runtime input corpora, never annotation files or expected results."""
import json
from .core import AppError


def replay_catalog(settings):
    catalog=[]
    specs = [
        (
            'lbj_daily',
            'LeBron James 2026 逐日模拟搜索池（零泄题 A/B）',
            'lbj_2026_daily_sim/all.json',
            [
                '2026-06-28T23:59:59Z',
                '2026-06-30T23:59:59Z',
                '2026-07-02T23:59:59Z',
                '2026-07-03T23:59:59Z',
                '2026-07-06T23:59:59Z',
                '2026-07-10T23:59:59Z',
                '2026-07-14T23:59:59Z',
                '2026-07-21T23:59:59Z',
                '2026-07-24T23:59:59Z',
                '2026-07-28T23:59:59Z',
            ],
        ),
        (
            'mcd_hk_real',
            '香港麥當勞 2026 優惠與聯乘逐階段模擬池（51 條真鏈）',
            'mcd_hk_real_2026/all.json',
            [
                '2026-09-05T23:59:59Z',
                '2026-09-24T23:59:59Z',
                '2026-10-09T23:59:59Z',
                '2026-10-31T23:59:59Z',
                '2026-11-18T23:59:59Z',
                '2026-11-23T23:59:59Z',
            ],
        ),
        ('lbj', 'LeBron James 转会追踪', 'lbj_2026_extended/all.json', None),
        ('nba', 'NBA 多事件追踪', 'nba_2026_multi_event/all.json', None),
    ]
    for identity, name, path, fixed_cutoffs in specs:
        full = settings.corpus_root / path
        if not full.exists():
            continue
        try:
            rows=json.loads(full.read_text(encoding='utf-8'))
            dates=sorted({r['published_at'] for r in rows})
        except (ValueError,KeyError,TypeError):
            raise AppError('INVALID_CORPUS', 'A bundled corpus could not be read.', 422)
        cutoffs = fixed_cutoffs if fixed_cutoffs else (
            list(dict.fromkeys(dates[min(len(dates)-1, int(len(dates)*n/3)-1)] for n in (1,2,3))) if dates else []
        )
        catalog.append(dict(corpus_id=identity,name=name,corpus_path=path,records=len(rows),cutoffs=cutoffs,
                            provenance='全真 Google News 链接离线模拟语料（无未来信息泄露）'))
    return catalog
