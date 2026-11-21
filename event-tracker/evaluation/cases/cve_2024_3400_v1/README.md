# CVE-2024-3400：多来源单事件开发案例 v1

**Case Brief。** Topic 仅追踪 CVE-2024-3400 在 PAN-OS GlobalProtect 中的披露、实际利用、修复可用性和后续检测。窗口为 2024 年 4 月 12 日至 5 月 15 日（UTC）。这些是**一个漏洞 Event 的不同状态维度**，不因发布日期或来源不同而拆成多个 Event。其他独立 CVE 才可能构成另一个漏洞 Event。证据来自厂商公告、独立研究机构 Volexity，以及一条时间和历史版本均未核实的 NVD 汇总材料。

输入在 `../../../fixtures/cve_2024_3400_v1/all.json`，标准答案在本目录 `ground_truth.json`。所有 `content` 是评测者写的短摘要，附原始链接供复核。原始页面只有日期而无可核实的小时级发布时间，因此 fixture 用对应日期 00:00 UTC 作**排序占位**，不用于小时级时效评分。

| ID | 来源与用途 | 历史版本状态 |
|---|---|---|
| CVE3400-01/03/04 | [Palo Alto Networks 安全公告](https://security.paloaltonetworks.com/CVE-2024-3400)，分别依据 4 月 12 日初发、4 月 15 日修复可用、4 月 16 日指引更新记录 | 当前可见网页是持续更新后的版本；这些条目是按其 Timeline **回顾性整理的摘要**，不是当日页面快照。 |
| CVE3400-02 | [Volexity 4 月 12 日独立入侵报告](https://www.volexity.com/blog/2024/04/12/zero-day-exploitation-of-unauthenticated-remote-code-execution-vulnerability-in-globalprotect-cve-2024-3400/) | 需核查文章是否有事后编辑；只采纳可确认在 4 月 12 日公开的内容。 |
| CVE3400-05 | [Volexity 5 月 15 日后续检测分析](https://www.volexity.com/blog/2024/05/15/detecting-compromise-of-cve-2024-3400-on-palo-alto-networks-globalprotect-devices/) | 仅放最终阶段。 |

| CVE3400-06 | [NVD 汇总页](https://nvd.nist.gov/vuln/detail/CVE-2024-3400) | 首次发布时间和 2024 年 5 月 15 日的页面版本未核实。它被保留为**来源不确定性测试项**，不能算独立入侵观察。 |

`CVE3400-06.published_at` 是现有 ReplayConnector 必填的**回放占位时间**，用于将其放在最终阶段，绝不是经核实的真实发布时间。输入正文也明示这一点；`case.json` 和标准答案将它列为 `uncertain_source_ids`。它不参加时效性或历史事实召回评分，只人工检查 Agent 是否标明不确定性、避免将当前汇总倒填为过去事实。将来若接口支持 `published_at=null` 与独立的 `available_at`，应改用这两个字段，撤销占位时间。

**主要判定：** 全部材料属于同一 Event，但要分别维护“漏洞披露”“实际利用”“补丁可用”“客户设备是否部署”和“后续入侵检测”状态。4 月 12 日不能知道 4 月 15 日的修复可用性；补丁“已可用”不等于客户设备“已升级”，也不意味着入侵调查结束；5 月的检测指引不应被误判为补丁仍未发布。Volexity 的客户观察可作为与厂商公告不同的来源，不应重复创建漏洞 Event 或重复发同一里程碑通知。

**评分口径：** 按 `ground_truth.json` 的五个状态维度逐阶段人工判定，不用单一 `patched/resolved` 字样代替全部状态。对于 CVE3400-03，同一更新同时影响补丁可用性与缓解建议，仍只是一条 Evidence 和同一个 Event；对于 CVE3400-01/02，两个来源可共同支持实际利用，但不应按文章来源各建一个 Event。CVE3400-06 测不确定性处理，评分时不把它的占位日期当作事实。正式通知标签需第二人审议；当前为开发反馈预期。

标准答案采用统一 v2.0 模板。`provenance_status=uncertain` 和 `scoring_exclusions` 保留历史版本与时间不确定性；`milestones` 将通知绑定到发展事项，避免按文章数重复提醒。

**正式评分限制：** 厂商历史快照尚未找到，因而本版只能给组员做开发反馈，不可声称它是严格的无未来信息泄漏实验。厂商的 01/03/04 三条输入来自同一持续更新的 URL 的不同时间线摘要；现有连接器可能按 URL 将后续更新误去重，应将这种情况记录为来源版本处理缺口，不能直接当成 Agent 忽略进展。下一版应找到可验证的归档快照、当日独立公告或历史版本，逐条重写早期输入；第二人独立核对标准答案后再冻结。
