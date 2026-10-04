import unittest

import app as app_module


class ManagerFinancePermissionInheritanceTest(unittest.TestCase):
    def test_manager_contains_every_default_finance_menu_permission(self):
        missing = sorted(
            key for key, roles in app_module.DEFAULT_MENU_ROLES.items()
            if "finance" in roles and "manager" not in roles
        )
        self.assertEqual(missing, [])

    def test_manager_contains_every_default_finance_action_permission(self):
        missing = sorted(
            f"{resource}:{action}"
            for (resource, action), roles in app_module.DEFAULT_ACTION_ROLES.items()
            if "finance" in roles and "manager" not in roles
        )
        self.assertEqual(missing, [])

    def test_finance_override_is_effectively_inherited_by_manager(self):
        with app_module.app.test_request_context("/"):
            app_module.g.user = {"id": 1, "role": "manager"}
            app_module.g._menu_permission_overrides = {
                ("manager", "bank_accounts"): False,
                ("finance", "bank_accounts"): True,
            }
            app_module.g._action_permission_overrides = {
                ("manager", "bank_accounts", "edit"): False,
                ("finance", "bank_accounts", "edit"): True,
            }
            self.assertTrue(app_module.has_menu_permission("bank_accounts"))
            self.assertTrue(app_module.has_action_permission("bank_accounts", "edit"))


if __name__ == "__main__":
    unittest.main()
