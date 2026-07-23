from datetime import timedelta

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
