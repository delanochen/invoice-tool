"""Menu defaults and manager inheritance, preserving per-app state."""

def make_default_menu_roles(MENU_PERMISSION_GROUPS):
    return {item['key']: set(item['roles']) for group in MENU_PERMISSION_GROUPS for item in group['items']}

def apply_manager_inheritance(DEFAULT_MENU_ROLES, *, _assign_iteration):
    for _roles in DEFAULT_MENU_ROLES.values():
        _assign_iteration(_roles)
        if 'finance' in _roles:
            _roles.add('manager')
