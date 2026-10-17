"""Public names and physical-device groups; no credentials belong here."""

TARGETS = {
    'monitor-plug': ('monitor-plug', 'Monitor power plug'),
    'bedroom-light': ('bedroom-light', 'Bedroom light'),
    'ceiling-upper': ('ceiling-switch', 'Upper ceiling light'),
    'ceiling-lower': ('ceiling-switch', 'Lower ceiling light'),
    'ceiling-all': ('ceiling-switch', 'Both ceiling lights'),
}


def device_list():
    return [{'name': name, 'label': label, 'device': group,
             'actions': ['status', 'on', 'off']}
            for name, (group, label) in TARGETS.items()]
