"""Only runtime input corpora, never annotation files or expected results."""
import json
from .core import AppError


def replay_catalog(settings):
    catalog=[]
    specs = [
        (
            'lbj_daily',
            'LeBron James 2026 逐日模拟搜索池（53 条真链）',
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
            '跟踪转会进展，区分传闻、谈判、协议与正式签约；保留冲突证据。同一交易的后续进展关联原事件，独立交易分开追踪。',
            ['LeBron', 'LBJ', '詹姆斯'],
            '离线模拟搜索池（附 Google News 报道链接，按发布时间切片；新闻细节仍以原文为准）',
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
            '追踪香港麦当劳2026年真实套餐新品、联名活动、App优惠与套票发售、价格与有效期变动；同一活动的续期或加推关联原事件，不同活动分开建档，过滤无关社会杂闻与竞品广告。',
            ['麥當勞', '麦当劳', 'McDonald'],
            '离线模拟搜索池（附 Google News 报道链接，按发布时间切片；新闻细节仍以原文为准）',
        ),
        (
            'lbj',
            'LeBron James 转会追踪',
            'lbj_2026_extended/all.json',
            None,
            '跟踪转会进展，区分传闻、谈判、协议与正式签约；保留冲突证据。',
            None,
            '队友提供的离线测试语料，新闻真实性与标签仍需核验',
        ),
        (
            'nba',
            'NBA 多事件追踪',
            'nba_2026_multi_event/all.json',
            None,
            '跟踪多条独立转会与续约进展，独立交易分开建档并保留冲突证据。',
            None,
            '队友提供的离线测试语料，新闻真实性与标签仍需核验',
        ),
    ]
    for identity, name, path, fixed_cutoffs, default_goal, anchor_terms, provenance in specs:
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
        entry = dict(
            corpus_id=identity,
            name=name,
            corpus_path=path,
            records=len(rows),
            cutoffs=cutoffs,
            default_goal=default_goal,
            provenance=provenance,
        )
        if anchor_terms:
            entry['anchor_terms'] = anchor_terms
        catalog.append(entry)
    return catalog
