"""Deterministic in-app policy, applied only to validated state transitions."""

def decide_notification(update_kind, preference):
    priority, action = {
        'event_created': ('routine', 'digest'),
        'fact_added': ('routine', 'digest'),
        'major_milestone': ('breaking', 'immediate'),
        'minor_update': ('low', 'record_only'),
    }[update_kind]
    reasons = [update_kind]
    if action == 'immediate' and preference == 'digest_only':
        action = 'digest'
        reasons.append('digest_only_preference')
    return priority, action, reasons
