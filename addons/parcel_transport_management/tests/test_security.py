from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import ParcelTestCase


@tagged("post_install", "-at_install")
class TestParcelSecurity(ParcelTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.courier_group = cls.env.ref("parcel_transport_management.group_ptm_courier")
        cls.operator_group = cls.env.ref(
            "parcel_transport_management.group_ptm_operator"
        )
        cls.manager_group = cls.env.ref("parcel_transport_management.group_ptm_manager")
        internal_group = cls.env.ref("base.group_user")
        cls.courier_user = (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "PTM Courier User",
                    "login": "ptm-courier-test",
                    "email": "ptm-courier@example.test",
                    "company_id": cls.company.id,
                    "company_ids": [Command.set(cls.company.ids)],
                    "group_ids": [
                        Command.set((internal_group | cls.courier_group).ids)
                    ],
                }
            )
        )
        cls.operator_user = (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "PTM Operator User",
                    "login": "ptm-operator-test",
                    "email": "ptm-operator@example.test",
                    "company_id": cls.company.id,
                    "company_ids": [Command.set(cls.company.ids)],
                    "group_ids": [
                        Command.set((internal_group | cls.operator_group).ids)
                    ],
                }
            )
        )
        cls.manager_user = (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "PTM Manager User",
                    "login": "ptm-manager-test",
                    "email": "ptm-manager@example.test",
                    "company_id": cls.company.id,
                    "company_ids": [Command.set((cls.company | cls.other_company).ids)],
                    "group_ids": [
                        Command.set((internal_group | cls.manager_group).ids)
                    ],
                }
            )
        )

    def _courier_for_courier_user(self):
        courier = self.env["parcel.courier"].search(
            [("user_id", "=", self.courier_user.id)], limit=1
        )
        return courier or self.create_courier(user_id=self.courier_user.id)

    def _deliver(self, shipment):
        shipment.action_assign(self.create_courier().id)
        shipment.action_record_pickup(shipment.package_ids.ids)
        shipment.action_start_transit()
        shipment.action_record_delivery(
            shipment.package_ids.ids,
            recipient_name="Receiving Clerk",
        )

    def _start_transit(self, shipment, courier):
        shipment.action_assign(courier.id)
        shipment.action_record_pickup(shipment.package_ids.ids)
        shipment.action_start_transit()

    def test_delivery_zones_are_operational_navigation_for_workers(self):
        zone_menu = self.env.ref("parcel_transport_management.menu_ptm_zones")
        operations_menu = self.env.ref(
            "parcel_transport_management.menu_ptm_operations"
        )
        configuration_menu = self.env.ref(
            "parcel_transport_management.menu_ptm_configuration"
        )

        self.assertEqual(zone_menu.parent_id, operations_menu)
        self.assertIn(self.courier_group, zone_menu.group_ids)
        self.assertIn(self.operator_group, zone_menu.group_ids)
        self.assertNotIn(self.manager_group, configuration_menu.group_ids)
        self.assertTrue(
            self.manager_user.has_group(
                "parcel_transport_management.group_ptm_operator"
            )
        )

    def test_manager_can_cancel_but_operator_cannot(self):
        manager_shipment = self.create_shipment()
        operator_shipment = self.create_shipment()

        manager_shipment.with_user(self.manager_user).action_cancel(
            "Customer requested cancellation"
        )

        self.assertEqual(manager_shipment.state, "cancelled")
        with self.assertRaises(AccessError):
            operator_shipment.with_user(self.operator_user).action_cancel(
                "Customer requested cancellation"
            )
        self.assertEqual(operator_shipment.state, "draft")

    def test_delivered_shipment_cannot_be_cancelled_by_manager(self):
        shipment = self.create_shipment()
        self._deliver(shipment)

        with self.assertRaises(UserError):
            shipment.with_user(self.manager_user).action_cancel(
                "Cancellation requested after delivery"
            )

        self.assertEqual(shipment.state, "delivered")

    def test_operational_fields_cannot_be_written_directly(self):
        protected_values = (
            {"state": "assigned"},
            {"courier_id": self.courier.id},
            {"original_expected_delivery_at": fields.Datetime.now()},
        )
        users = (self.courier_user, self.operator_user, self.manager_user)

        for user in users:
            for values in protected_values:
                shipment = self.create_shipment()
                with self.subTest(user=user.login, field=next(iter(values))):
                    with self.assertRaises(AccessError):
                        shipment.with_user(user).write(values)

    def test_expected_delivery_is_editable_only_before_assignment(self):
        shipment = self.create_shipment()
        expected_delivery_at = fields.Datetime.now() + timedelta(days=1)

        shipment.with_user(self.operator_user).write(
            {"expected_delivery_at": expected_delivery_at}
        )
        shipment.with_user(self.operator_user).action_assign(self.courier.id)

        self.assertEqual(shipment.expected_delivery_at, expected_delivery_at)
        self.assertEqual(
            shipment.original_expected_delivery_at,
            expected_delivery_at,
        )
        with self.assertRaises(UserError):
            shipment.with_user(self.manager_user).write(
                {"expected_delivery_at": expected_delivery_at + timedelta(days=1)}
            )

    def test_forged_context_cannot_bypass_operational_write_guards(self):
        shipment = self.create_shipment()
        forged = shipment.with_user(self.manager_user).with_context(
            ptm_internal_write=True,
            allow_operational_write=True,
            skip_state_validation=True,
        )

        with self.assertRaises(AccessError):
            forged.write(
                {
                    "state": "assigned",
                    "courier_id": self.courier.id,
                }
            )

        self.assertEqual(shipment.state, "draft")
        self.assertFalse(shipment.courier_id)

    def test_courier_can_operate_only_assigned_shipments(self):
        own_courier = self._courier_for_courier_user()
        other_courier = self.create_courier()
        own_shipment = self.create_shipment()
        other_shipment = self.create_shipment()
        own_shipment.action_assign(own_courier.id)
        other_shipment.action_assign(other_courier.id)

        own_shipment.with_user(self.courier_user).action_record_pickup(
            own_shipment.package_ids.ids
        )

        self.assertEqual(own_shipment.state, "picked_up")
        self.assertTrue(own_shipment.package_ids.pickup_event_id)
        with self.assertRaises(AccessError):
            other_shipment.with_user(self.courier_user).action_record_pickup(
                other_shipment.package_ids.ids
            )
        self.assertEqual(other_shipment.state, "assigned")
        self.assertFalse(other_shipment.package_ids.pickup_event_id)

    def test_unauthorized_operational_calls_are_rejected_before_locking(self):
        self._courier_for_courier_user()
        shipment = self.create_shipment()
        shipment.action_assign(self.create_courier().id)
        package_ids = shipment.package_ids.ids
        unauthorized = shipment.with_user(self.courier_user)
        operations = (
            (
                "pickup",
                lambda: unauthorized.action_record_pickup(package_ids),
            ),
            ("transit", unauthorized.action_start_transit),
            (
                "delivery",
                lambda: unauthorized.action_record_delivery(
                    package_ids,
                    recipient_name="Receiving Clerk",
                ),
            ),
            (
                "delivery_failure",
                lambda: unauthorized.action_record_delivery_failure(
                    "Unauthorized attempt"
                ),
            ),
        )

        for operation_name, operation in operations:
            with self.subTest(operation=operation_name):
                with patch.object(
                    type(unauthorized),
                    "_lock_shipments",
                    side_effect=AssertionError("unauthorized call acquired a row lock"),
                ) as lock_shipments:
                    with self.assertRaises(AccessError):
                        operation()
                    lock_shipments.assert_not_called()

    def test_cross_company_operator_is_rejected_before_locking(self):
        shipment = self.create_shipment(
            company=self.other_company,
            sender=self.other_sender,
            recipient=self.other_recipient,
        )
        shipment.action_assign(self.create_courier(company=self.other_company).id)
        unauthorized = shipment.with_user(self.operator_user).with_context(
            allowed_company_ids=self.company.ids,
        )

        with patch.object(
            type(unauthorized),
            "_lock_shipments",
            side_effect=AssertionError("unauthorized call acquired a row lock"),
        ) as lock_shipments:
            with self.assertRaises(AccessError):
                unauthorized.action_start_transit()
            lock_shipments.assert_not_called()

    def test_dispatch_and_manager_calls_reject_cross_company_before_locking(self):
        other_courier = self.create_courier(company=self.other_company)
        other_reassignment_courier = self.create_courier(
            company=self.other_company,
            name="Other-company reassignment courier",
        )
        draft = self.create_shipment(
            company=self.other_company,
            sender=self.other_sender,
            recipient=self.other_recipient,
            expected_delivery_at=fields.Datetime.now() + timedelta(days=1),
        )
        assigned = self.create_shipment(
            company=self.other_company,
            sender=self.other_sender,
            recipient=self.other_recipient,
        )
        assigned.action_assign(other_courier.id)
        failed = self.create_shipment(
            company=self.other_company,
            sender=self.other_sender,
            recipient=self.other_recipient,
        )
        self._start_transit(failed, other_courier)
        failed.action_record_delivery_failure("Other-company delivery failure")

        operator_context = {"allowed_company_ids": self.company.ids}
        manager_context = {"allowed_company_ids": self.company.ids}
        operations = (
            (
                "assign",
                lambda: (
                    draft.with_user(self.operator_user)
                    .with_context(**operator_context)
                    .action_assign(other_courier.id)
                ),
            ),
            (
                "unassign",
                lambda: (
                    assigned.with_user(self.operator_user)
                    .with_context(**operator_context)
                    .action_unassign()
                ),
            ),
            (
                "reassign",
                lambda: (
                    assigned.with_user(self.operator_user)
                    .with_context(**operator_context)
                    .action_reassign(other_reassignment_courier.id)
                ),
            ),
            (
                "retry",
                lambda: (
                    failed.with_user(self.operator_user)
                    .with_context(**operator_context)
                    .action_retry_delivery(
                        other_reassignment_courier.id,
                        "Unauthorized retry",
                    )
                ),
            ),
            (
                "cancel",
                lambda: (
                    draft.with_user(self.manager_user)
                    .with_context(**manager_context)
                    .action_cancel("Unauthorized cancellation")
                ),
            ),
            (
                "revise_sla",
                lambda: (
                    draft.with_user(self.manager_user)
                    .with_context(**manager_context)
                    .action_revise_sla(
                        fields.Datetime.now() + timedelta(days=2),
                        "Unauthorized SLA revision",
                    )
                ),
            ),
            (
                "correct_route",
                lambda: (
                    draft.with_user(self.manager_user)
                    .with_context(**manager_context)
                    .action_correct_route(
                        {"pickup_zip": "28001"},
                        "Unauthorized route correction",
                    )
                ),
            ),
        )

        for operation_name, operation in operations:
            with self.subTest(operation=operation_name):
                with (
                    patch.object(
                        type(self.company),
                        "_lock_package_limits",
                        side_effect=AssertionError(
                            "unauthorized call acquired a company row lock"
                        ),
                    ) as lock_package_limits,
                    patch.object(
                        type(draft),
                        "_lock_shipments",
                        side_effect=AssertionError(
                            "unauthorized call acquired a shipment row lock"
                        ),
                    ) as lock_shipments,
                ):
                    with self.assertRaises(AccessError):
                        operation()
                    lock_package_limits.assert_not_called()
                    lock_shipments.assert_not_called()

    def test_untrusted_courier_ids_are_rejected_before_locking(self):
        other_company_courier = self.create_courier(company=self.other_company)
        local_courier = self.create_courier(name="Local assigned courier")
        draft = self.create_shipment()
        assigned = self.create_shipment()
        assigned.action_assign(local_courier.id)
        failed = self.create_shipment()
        self._start_transit(failed, local_courier)
        failed.action_record_delivery_failure("Local delivery failure")
        restricted_context = {"allowed_company_ids": self.company.ids}
        restricted_draft = draft.with_user(self.manager_user).with_context(
            **restricted_context
        )
        restricted_assigned = assigned.with_user(self.manager_user).with_context(
            **restricted_context
        )
        restricted_failed = failed.with_user(self.manager_user).with_context(
            **restricted_context
        )
        operations = (
            (
                "assign",
                lambda: restricted_draft.action_assign(other_company_courier.id),
            ),
            (
                "reassign",
                lambda: restricted_assigned.action_reassign(other_company_courier.id),
            ),
            (
                "retry",
                lambda: restricted_failed.action_retry_delivery(
                    other_company_courier.id,
                    "Unauthorized candidate",
                ),
            ),
        )

        for operation_name, operation in operations:
            with self.subTest(operation=operation_name):
                with (
                    patch.object(
                        type(self.company),
                        "_lock_package_limits",
                        side_effect=AssertionError(
                            "candidate was checked after company row locking"
                        ),
                    ) as lock_package_limits,
                    patch.object(
                        type(draft),
                        "_lock_shipments",
                        side_effect=AssertionError(
                            "candidate was checked after shipment row locking"
                        ),
                    ) as lock_shipments,
                    patch.object(
                        type(draft),
                        "_lock_couriers",
                        side_effect=AssertionError(
                            "candidate was checked after courier row locking"
                        ),
                    ) as lock_couriers,
                ):
                    with self.assertRaises(AccessError):
                        operation()
                    lock_package_limits.assert_not_called()
                    lock_shipments.assert_not_called()
                    lock_couriers.assert_not_called()

    def test_live_reassignment_requires_manager_before_locking(self):
        assigned_courier = self.create_courier(name="Live assigned courier")
        replacement_courier = self.create_courier(name="Live replacement courier")
        shipment = self.create_shipment()
        self._start_transit(shipment, assigned_courier)
        unauthorized = shipment.with_user(self.operator_user)

        with patch.object(
            type(unauthorized),
            "_lock_shipments",
            side_effect=AssertionError("manager check happened after row locking"),
        ) as lock_shipments:
            with self.assertRaises(AccessError):
                unauthorized.action_reassign(
                    replacement_courier.id,
                    reason="Unauthorized active reassignment",
                )
            lock_shipments.assert_not_called()

    def test_company_limit_write_checks_access_before_locking(self):
        self.create_shipment()
        unauthorized = self.company.with_user(self.courier_user)

        with patch.object(
            type(self.company),
            "_lock_package_limits",
            side_effect=AssertionError("company validation acquired a row lock"),
        ) as lock_package_limits:
            with self.assertRaises(AccessError):
                unauthorized.write({"parcel_max_package_weight": 29.0})
            lock_package_limits.assert_not_called()

    def test_shipment_and_package_crud_reject_access_before_locking(self):
        other_shipment = self.create_shipment(
            company=self.other_company,
            sender=self.other_sender,
            recipient=self.other_recipient,
        )
        other_package = self.create_package(other_shipment)
        local_draft = self.create_shipment()
        assigned = self.create_shipment()
        assigned_package = self.create_package(assigned)
        assigned.action_assign(self._courier_for_courier_user().id)
        restricted_context = {"allowed_company_ids": self.company.ids}
        restricted_shipment = other_shipment.with_user(self.operator_user).with_context(
            **restricted_context
        )
        restricted_package = other_package.with_user(self.operator_user).with_context(
            **restricted_context
        )
        package_model = self.env["parcel.package"]
        operations = (
            (
                "shipment_write",
                lambda: restricted_shipment.write(
                    {"expected_delivery_at": fields.Datetime.now() + timedelta(days=1)}
                ),
            ),
            (
                "package_create_without_acl",
                lambda: package_model.with_user(self.courier_user).create(
                    {"shipment_id": local_draft.id, "weight": 1.0}
                ),
            ),
            (
                "package_create_cross_company",
                lambda: (
                    package_model.with_user(self.operator_user)
                    .with_context(**restricted_context)
                    .create({"shipment_id": other_shipment.id, "weight": 1.0})
                ),
            ),
            (
                "package_write_cross_company",
                lambda: restricted_package.write({"weight": 2.0}),
            ),
            (
                "package_unlink_cross_company",
                lambda: restricted_package.unlink(),
            ),
            (
                "package_unlink_without_acl",
                lambda: assigned_package.with_user(self.courier_user).unlink(),
            ),
        )

        for operation_name, operation in operations:
            with self.subTest(operation=operation_name):
                with (
                    patch.object(
                        type(self.company),
                        "_lock_package_limits",
                        side_effect=AssertionError(
                            "unauthorized CRUD call acquired a company row lock"
                        ),
                    ) as lock_package_limits,
                    patch.object(
                        type(other_shipment),
                        "_lock_shipments",
                        side_effect=AssertionError(
                            "unauthorized CRUD call acquired a shipment row lock"
                        ),
                    ) as lock_shipments,
                ):
                    with self.assertRaises(AccessError):
                        operation()
                    lock_package_limits.assert_not_called()
                    lock_shipments.assert_not_called()

    def test_inline_package_create_checks_access_before_locking(self):
        shipment = self.create_shipment()
        shipment.action_assign(self._courier_for_courier_user().id)
        unauthorized = shipment.with_user(self.courier_user)

        with patch.object(
            type(self.company),
            "_lock_package_limits",
            side_effect=AssertionError(
                "unauthorized inline package create acquired a company row lock"
            ),
        ) as lock_package_limits:
            with self.assertRaises(AccessError):
                unauthorized.write(
                    {
                        "package_ids": [
                            Command.create(
                                {
                                    "weight": 1.0,
                                    "weight_uom_id": self.kg_uom.id,
                                }
                            )
                        ]
                    }
                )
            lock_package_limits.assert_not_called()

    def test_assigned_package_weight_write_rejects_state_before_locking(self):
        shipment = self.create_shipment()
        shipment.action_assign(self._courier_for_courier_user().id)
        package = shipment.package_ids.with_user(self.courier_user)

        with patch.object(
            type(self.company),
            "_lock_package_limits",
            side_effect=AssertionError(
                "invalid package write acquired a company row lock"
            ),
        ) as lock_package_limits:
            with self.assertRaises(UserError):
                package.write({"weight": 2.0})
            lock_package_limits.assert_not_called()

    def test_company_rules_hide_shipments_outside_allowed_companies(self):
        other_company_shipment = self.create_shipment(
            company=self.other_company,
            sender=self.other_sender,
            recipient=self.other_recipient,
        )

        with self.assertRaises(AccessError):
            other_company_shipment.with_user(self.operator_user).read(["state"])

        self.assertEqual(
            other_company_shipment.with_user(self.manager_user).company_id,
            self.other_company,
        )

    def test_operator_cannot_create_shipment_in_unauthorized_company(self):
        neutral_sender = self.env["res.partner"].create(
            {
                "name": "Company-neutral Sender",
                "company_id": False,
                "country_id": self.country.id,
            }
        )
        neutral_recipient = self.env["res.partner"].create(
            {
                "name": "Company-neutral Recipient",
                "company_id": False,
                "country_id": self.country.id,
            }
        )
        shipment_model = self.env["parcel.shipment"].with_user(self.operator_user)
        shipment_count = self.env["parcel.shipment"].sudo().search_count([])

        with self.assertRaises(AccessError):
            shipment_model.create(
                {
                    "company_id": self.other_company.id,
                    "sender_id": neutral_sender.id,
                    "recipient_id": neutral_recipient.id,
                    "package_ids": [
                        Command.create(
                            {
                                "weight": 1.0,
                                "weight_uom_id": self.kg_uom.id,
                            }
                        )
                    ],
                }
            )

        self.assertEqual(
            self.env["parcel.shipment"].sudo().search_count([]),
            shipment_count,
        )

    def test_shipment_company_is_immutable_and_failed_move_preserves_packages(self):
        neutral_sender = self.env["res.partner"].create(
            {"name": "Move-neutral Sender", "company_id": False}
        )
        neutral_recipient = self.env["res.partner"].create(
            {"name": "Move-neutral Recipient", "company_id": False}
        )
        shipment = self.create_shipment(
            sender=neutral_sender,
            recipient=neutral_recipient,
        )
        package = shipment.package_ids

        with self.assertRaises(AccessError):
            shipment.with_user(self.operator_user).write(
                {"company_id": self.other_company.id}
            )

        self.assertEqual(shipment.company_id, self.company)
        self.assertEqual(package.company_id, self.company)
        self.assertTrue(package.exists())

    def test_courier_profile_is_read_only_for_courier_role(self):
        courier = self._courier_for_courier_user()
        original_values = {
            "name": courier.name,
            "company_id": courier.company_id,
            "user_id": courier.user_id,
            "max_concurrent_shipments": courier.max_concurrent_shipments,
            "max_concurrent_weight": courier.max_concurrent_weight,
            "max_weight_uom_id": courier.max_weight_uom_id,
            "zone_ids": courier.zone_ids,
        }
        forbidden_updates = (
            {"name": "Forged Courier Identity"},
            {"user_id": False},
            {"max_concurrent_shipments": 999},
            {"max_concurrent_weight": 999.0},
            {"max_weight_uom_id": self.lb_uom.id},
            {"zone_ids": [Command.clear()]},
        )

        for values in forbidden_updates:
            with self.subTest(field=next(iter(values))):
                with self.assertRaises(AccessError):
                    courier.with_user(self.courier_user).write(values)
        with self.assertRaises(ValidationError):
            courier.with_user(self.courier_user).write(
                {"company_id": self.other_company.id}
            )

        for field_name, value in original_values.items():
            self.assertEqual(courier[field_name], value)

    def test_mixed_courier_operator_role_keeps_operator_visibility(self):
        own_courier = self._courier_for_courier_user()
        own_shipment = self.create_shipment()
        other_shipment = self.create_shipment()
        other_company_shipment = self.create_shipment(
            company=self.other_company,
            sender=self.other_sender,
            recipient=self.other_recipient,
        )
        own_shipment.action_assign(own_courier.id)
        self.courier_user.write({"group_ids": [Command.link(self.operator_group.id)]})

        visible = (
            self.env["parcel.shipment"]
            .with_user(self.courier_user)
            .search([("id", "in", (own_shipment | other_shipment).ids)])
        )

        self.assertEqual(visible, own_shipment | other_shipment)
        self.assertFalse(
            self.env["parcel.shipment"]
            .with_user(self.courier_user)
            .search([("id", "=", other_company_shipment.id)])
        )

    def test_reassignment_history_respects_role_company_and_ownership(self):
        own_courier = self._courier_for_courier_user()
        own_shipment = self.create_shipment()
        own_shipment.action_assign(self.courier.id)
        own_shipment.with_user(self.operator_user).action_reassign(own_courier.id)
        own_history = own_shipment.courier_reassignment_ids

        other_shipment = self.create_shipment()
        other_shipment.action_assign(self.create_courier().id)
        other_shipment.with_user(self.operator_user).action_reassign(
            self.create_courier().id
        )
        other_history = other_shipment.courier_reassignment_ids

        other_company_shipment = self.create_shipment(
            company=self.other_company,
            sender=self.other_sender,
            recipient=self.other_recipient,
        )
        other_company_shipment.action_assign(
            self.create_courier(company=self.other_company).id
        )
        other_company_shipment.action_reassign(
            self.create_courier(company=self.other_company).id
        )
        other_company_history = other_company_shipment.courier_reassignment_ids

        courier_visible = (
            self.env["parcel.courier.reassignment"]
            .with_user(self.courier_user)
            .search([("id", "in", (own_history | other_history).ids)])
        )
        operator_visible = (
            self.env["parcel.courier.reassignment"]
            .with_user(self.operator_user)
            .search(
                [
                    (
                        "id",
                        "in",
                        (own_history | other_history | other_company_history).ids,
                    )
                ]
            )
        )

        self.assertEqual(courier_visible, own_history)
        self.assertEqual(operator_visible, own_history | other_history)

    def test_cross_company_courier_assignment_is_rejected(self):
        shipment = self.create_shipment(company=self.company)
        other_company_courier = self.create_courier(company=self.other_company)

        with self.assertRaises(UserError):
            shipment.with_user(self.manager_user).action_assign(
                other_company_courier.id
            )

        self.assertFalse(shipment.courier_id)
        self.assertEqual(shipment.state, "draft")

    def test_delivery_attempt_and_retry_are_append_only_and_create_guarded(self):
        courier = self.create_courier()
        shipment = self.create_shipment()
        self._start_transit(shipment, courier)
        attempt = shipment.action_record_delivery_failure("Legitimate delivery failure")
        retry = shipment.with_user(self.operator_user).action_retry_delivery(
            courier.id,
            "Legitimate retry dispatch",
        )
        attempt_values = {
            "shipment_id": shipment.id,
            "courier_id": courier.id,
            "confirmed_by_id": self.manager_user.id,
            "occurred_at": fields.Datetime.now(),
            "reason": "Forged attempt",
            "package_ids": [Command.set(shipment.package_ids.ids)],
        }
        retry_values = {
            "attempt_id": attempt.id,
            "previous_courier_id": courier.id,
            "new_courier_id": courier.id,
            "dispatched_by_id": self.manager_user.id,
            "occurred_at": fields.Datetime.now(),
            "reason": "Forged retry",
        }

        with self.assertRaises(AccessError):
            (
                self.env["parcel.delivery.attempt"]
                .with_user(self.manager_user)
                .create(attempt_values)
            )
        with self.assertRaises(AccessError):
            (
                self.env["parcel.delivery.retry"]
                .with_user(self.manager_user)
                .create(retry_values)
            )
        with self.assertRaises(AccessError):
            attempt.with_user(self.manager_user).write({"reason": "Rewritten"})
        with self.assertRaises(AccessError):
            retry.with_user(self.manager_user).write({"reason": "Rewritten"})
        with self.assertRaises(AccessError):
            attempt.with_user(self.manager_user).unlink()
        with self.assertRaises(AccessError):
            retry.with_user(self.manager_user).unlink()
        with self.assertRaises(AccessError):
            shipment.package_ids.with_user(self.manager_user).write(
                {"delivery_attempt_ids": [Command.clear()]}
            )

        self.assertEqual(attempt.reason, "Legitimate delivery failure")
        self.assertEqual(retry.reason, "Legitimate retry dispatch")
        self.assertTrue(attempt.exists())
        self.assertTrue(retry.exists())
        self.assertEqual(shipment.package_ids.delivery_attempt_ids, attempt)

    def test_assigned_courier_can_fail_only_own_shipment_and_operator_can_retry(self):
        own_courier = self._courier_for_courier_user()
        other_courier = self.create_courier()
        retry_courier = self.create_courier(name="Operator Retry Courier")
        own_shipment = self.create_shipment()
        other_shipment = self.create_shipment()
        self._start_transit(own_shipment, own_courier)
        self._start_transit(other_shipment, other_courier)

        attempt = own_shipment.with_user(
            self.courier_user
        ).action_record_delivery_failure("Recipient site was inaccessible")

        self.assertEqual(attempt.confirmed_by_id, self.courier_user)
        self.assertEqual(attempt.courier_id, own_courier)
        self.assertEqual(own_shipment.state, "delivery_failed")
        self.assertFalse(own_shipment.courier_id)

        with self.assertRaises(AccessError):
            other_shipment.with_user(self.courier_user).action_record_delivery_failure(
                "Attempt against another route"
            )

        self.assertEqual(other_shipment.state, "in_transit")
        self.assertEqual(other_shipment.courier_id, other_courier)
        self.assertFalse(other_shipment.delivery_attempt_ids)

        retry = own_shipment.with_user(self.operator_user).action_retry_delivery(
            retry_courier.id,
            "Operator dispatched a new route",
        )

        self.assertEqual(retry.dispatched_by_id, self.operator_user)
        self.assertEqual(retry.attempt_id, attempt)
        self.assertEqual(own_shipment.state, "in_transit")
        self.assertEqual(own_shipment.courier_id, retry_courier)

    def test_delivery_attempts_and_retries_are_isolated_by_company(self):
        local_shipment = self.create_shipment()
        local_courier = self.create_courier()
        self._start_transit(local_shipment, local_courier)
        local_attempt = local_shipment.action_record_delivery_failure("Local failure")
        local_retry = local_shipment.action_retry_delivery(
            local_courier.id,
            "Local retry",
        )

        other_shipment = self.create_shipment(
            company=self.other_company,
            sender=self.other_sender,
            recipient=self.other_recipient,
        )
        other_courier = self.create_courier(company=self.other_company)
        self._start_transit(other_shipment, other_courier)
        other_attempt = other_shipment.action_record_delivery_failure(
            "Other-company failure"
        )
        other_retry = other_shipment.action_retry_delivery(
            other_courier.id,
            "Other-company retry",
        )

        attempts = local_attempt | other_attempt
        retries = local_retry | other_retry
        operator_attempts = (
            self.env["parcel.delivery.attempt"]
            .with_user(self.operator_user)
            .with_context(allowed_company_ids=self.company.ids)
            .search([("id", "in", attempts.ids)])
        )
        operator_retries = (
            self.env["parcel.delivery.retry"]
            .with_user(self.operator_user)
            .with_context(allowed_company_ids=self.company.ids)
            .search([("id", "in", retries.ids)])
        )
        manager_attempts = (
            self.env["parcel.delivery.attempt"]
            .with_user(self.manager_user)
            .with_context(allowed_company_ids=(self.company | self.other_company).ids)
            .search([("id", "in", attempts.ids)])
        )
        manager_retries = (
            self.env["parcel.delivery.retry"]
            .with_user(self.manager_user)
            .with_context(allowed_company_ids=(self.company | self.other_company).ids)
            .search([("id", "in", retries.ids)])
        )

        self.assertEqual(set(operator_attempts.ids), set(local_attempt.ids))
        self.assertEqual(set(operator_retries.ids), set(local_retry.ids))
        self.assertEqual(set(manager_attempts.ids), set(attempts.ids))
        self.assertEqual(set(manager_retries.ids), set(retries.ids))
        self.assertEqual(local_attempt.company_id, self.company)
        self.assertEqual(local_retry.company_id, self.company)
        self.assertEqual(other_attempt.company_id, self.other_company)
        self.assertEqual(other_retry.company_id, self.other_company)
        self.assertEqual(
            local_shipment.package_ids.delivery_attempt_ids,
            local_attempt,
        )
        self.assertEqual(
            other_shipment.package_ids.delivery_attempt_ids,
            other_attempt,
        )
