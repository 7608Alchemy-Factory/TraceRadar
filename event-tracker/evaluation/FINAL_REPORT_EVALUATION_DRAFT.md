# Evaluation

We evaluated TraceRadar along the five dimensions in our proposal: information relevance, topic evolution, event state updates, notification quality, and evidence grounding and trace consistency. An Event is the system's record of a developing story or offer; new articles can update an existing Event or justify a separate one. We first tested how the agent handled a fixed set of articles, then tested live discovery separately. The fixed-evidence experiment covered four cases: a vulnerability investigation, a Hong Kong McDonald's campaign, a notice affecting two bus routes, and a returning promotion. We ran each case three times from a fresh state, presenting that case's articles in the same order and at the same stages on every run. The first two runs provide the scored results, while the third illustrates how output varied under identical inputs. We used deepseek-flash, prompt version assessment-v14, at most two model attempts and a 30-second request timeout.

## Reference answers and scoring

Each benchmark case paired a tracking topic and observation times with fixed articles containing source, publication time and content. Before running the agent, we specified which articles were relevant, which Event each belonged to, the facts and status expected at each stage, and the notification action expected for each significant development. These reference answers stayed outside the agent's input. We scored the Event facts and notifications saved by the agent. An AI assistant prepared the initial comparisons, and the evaluation author reviewed the required-fact matches, disputed Event boundaries, notification decisions and failure cases using the following rules.

For relevance, an article counted as correct when the agent's include/exclude decision matched its reference label; a duplicate could be relevant without requiring another update. For topic evolution, we matched Event identity and scope at each stage, then checked whether later articles updated the appropriate Event or required a separate one. For state updates, a required fact counted only when its subject, condition, number or date, and Event assignment agreed with the reference at the first stage where its evidence was visible. For notifications, we aligned each expected development with the generated action (immediate, digest, or record_only); record_only means saving a change without notifying the user. The evaluation author accepted a different action only where its urgency was reasonable. For grounding and trace consistency, we checked whether each cited article ID existed and was available when the fact was saved. The required facts were reviewed for content separately; the 81-reference count reports whether source links in the saved output point to available articles.

## Fixed-evidence results

| Dimension | Measured result from two reviewed runs | Capability demonstrated |
|---|---|---|
| Information relevance | In both the first and second runs, the agent selected the same 16 articles labeled relevant to the tracked topics and excluded the one unrelated article. | The agent can filter supplied articles by their relevance to a user's tracking topic. |
| Topic evolution | The correct Event existed at 44 of 46 expected checkpoints across the two runs. | The agent can keep later reports attached to an ongoing Event and create multiple Events from a single report when it describes distinct developments. |
| Event state updates | 51 of 52 required facts were recorded in the correct Event when their supporting articles first appeared. | The agent can incorporate newly reported facts, including dates, prices and conditions, into the appropriate Event state. |
| Notifications | The evaluation author accepted the action for 28 of 30 evidence-driven developments. | The agent can choose between an immediate alert, a digest and a record-only action for developments found in new evidence. |
| Evidence grounding and trace consistency | All 81 saved fact-to-article references pointed to an article available at that stage. | The agent can retain a source reference for each recorded fact, allowing its origin to be inspected. |

To compare the Event-tracking result with a simpler approach, we ran a deterministic baseline on the same articles and stages. It treated every article as relevant, created one Event per article and issued one digest, without deduplication or updates to existing Events. Across 12 stages, its mean absolute Event-count error was 1.667 per stage, compared with 0.167 for the agent's first run and 0 for its second. Because both methods received the same articles at the same times, this comparison isolates Event grouping; it does not compare their ability to find articles online.

## Representative, challenging and failure cases

### Representative updates

Our synthetic CedarDesk case followed a file-preview vulnerability from an initial advisory through confirmed exploitation, mitigation and patch release. The agent kept these successive developments in one Event. In a separate synthetic Eastbank Coffee case, a breakfast offer returned at 22 yuan after an earlier 20-yuan round. The agent reused the existing Event and recorded the new price. Together, these cases illustrate how a tracked subject can develop without creating a new Event for every article.

### Challenging evidence

Some articles required the agent to separate developments within a single source. In a synthetic Bay Transit case, one notice announced different service changes for routes 12 and 27, followed by a later notice restoring route 12. The agent created two Events from the first notice and later updated only the relevant route. The curated Hong Kong McDonald's case tested a similar boundary decision: one announcement introduced prepaid vouchers and a separate $3 tea offer with different terms and follow-up dates. CedarDesk added two other challenges: a repeated patch report should not trigger another notification, and an anonymous rumor should remain unverified.

### Observed failures and variability

The McDonald's announcement shows where the Event boundary decision failed. The voucher exchange coupons were usable through 9 November, although the voucher sale end date was not specified; the tea offer ran from 28 September to 2 October. The reference answer therefore tracked the voucher program and tea offer as separate Events supported by the same article.

| Reference outcome | First run | Second run |
|---|---|---|
| Two Events from one announcement: prepaid vouchers and a time-limited tea offer. | The agent merged both offers into one Event. | The agent created two Events but also copied the tea fact into the voucher Event. |

In CedarDesk, the agent avoided a duplicate patch notification but treated the unverified rumor as a confirmed conflict. One run also assigned mitigation a lower notification priority than the reference.

The repeated runs also showed variability. At the McDonald's case's final stage, the agent had 4 Events in run one, 5 in run two and 3 in run three, compared with 5 in the reference. In the third run, the model proposed facts for one offer but marked its relationship to existing Events as unresolved. The system sent that proposal for review and did not save the Event or its facts. Because the third run's content decisions were not fully reviewed, it illustrates variability but is not included in the two-run scores.

## Search-only evaluation

The fixed-evidence runs began with articles already supplied to the agent, so they could not show whether it would find useful news on its own. For the search test, we supplied the topic of Hong Kong McDonald's activities and a window from 11 to 24 November 2026. We compared the articles returned by a fixed search query, model-generated queries and an RSS query, then judged whether those articles matched the topic. This test ended with the returned articles: it did not evaluate how the agent would turn them into Events or notifications. A candidate was relevant if it reported a newly published or newly starting offer, limited food item or membership activity within the window; old offer roundups and unrelated topics were excluded. We also listed three known developments before searching and counted one as found when a returned headline matched it.

A single fixed query returned 20 candidates, three model-generated queries returned 24, and one RSS query returned 20. The AI assistant prepared relevance labels, which the evaluation author reviewed and accepted: 17/20, 19/24 and 16/20 candidates were relevant, respectively. All three methods surfaced headlines for the three prelisted developments. In this trial, the generated queries therefore did not discover an additional known development despite using three searches instead of one. These figures describe the returned candidate pools, not all relevant articles on the web; the unequal query budgets prevent a controlled ranking-quality comparison.

## Interpretation and limitations

On the fixed articles, TraceRadar usually identified the right Events and captured the facts required by the reference answers. The baseline comparison supports its ability to group reports into evolving Events, while the cases show where that grouping failed. The search-only test surfaced headlines for all three prelisted developments, but the small known-development set does not establish how completely the agent discovers relevant news. The fixed-evidence dataset is small and mostly synthetic, the baseline is intentionally simple, and the required-fact score excludes additional statements generated by the agent. We therefore keep the fixed-evidence and search results separate rather than combining them into one system accuracy figure.
