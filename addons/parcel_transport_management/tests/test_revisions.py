from datetime import timedelta

from lxml import etree
from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import ParcelTestCase


@tagged("post_install", "-at_install")
class TestParcelRevisions(ParcelTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        internal_group = cls.env.ref("base.group_user")
        manager_group = cls.env.ref("parcel_transport_management.group_ptm_manager")
        operator_group = cls.env.ref("parcel_transport_management.group_ptm_operator")
        cls.manager_user = (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "PTM Revision Manager",
                    "login": "ptm-revision-manager-test",
                    "email": "ptm-revision-manager@example.test",
                    "company_id": cls.company.id,
                    "company_ids": [Command.set(cls.company.ids)],
                    "group_ids": [Command.set((internal_group | manager_group).ids)],
                }
            )
        )
        cls.operator_user = (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "PTM Revision Operator",
                    "login": "ptm-revision-operator-test",
                    "email": "ptm-revision-operator@example.test",
                    "company_id": cls.company.id,
                    "company_ids": [Command.set(cls.company.ids)],
                    "group_ids": [Command.set((internal_group | operator_group).ids)],
                }
            )
        )

    def _deliver(self, shipment, courier=None):
        shipment.action_assign((courier or self.create_courier()).id)
        shipment.action_record_pickup(shipment.package_ids.ids)
        shipment.action_start_transit()
        shipment.action_record_delivery(
            shipment.package_ids.ids,
            recipient_name="Receiving Clerk",
        )

    def test_assignment_sets_original_and_current_sla_once(self):
        shipment = self.create_shipment()
        self.assertFalse(shipment.original_expected_delivery_at)
        self.assertFalse(shipment.expected_delivery_at)

        shipment.action_assign(self.courier.id)

        self.assertTrue(shipment.expected_delivery_at)
        self.assertEqual(
            shipment.original_expected_delivery_at,
            shipment.expected_delivery_at,
        )

    def test_manager_revision_requires_reason_and_preserves_history(self):
        shipment = self.create_shipment()
        shipment.action_assign(self.courier.id)
        original_sla = shipment.original_expected_delivery_at
        previous_sla = shipment.expected_delivery_at
        revised_sla = previous_sla + timedelta(hours=6)

        with self.assertRaises(UserError):
            shipment.with_user(self.manager_user).action_revise_sla(
                revised_sla,
                "",
            )
        self.assertEqual(shipment.expected_delivery_at, previous_sla)
        self.assertFalse(shipment.sla_revision_ids)
        before = fields.Datetime.now()

        shipment.with_user(self.manager_user).action_revise_sla(
            revised_sla,
            "Weather disruption",
        )

        after = fields.Datetime.now()
        revision = shipment.sla_revision_ids
        self.assertEqual(len(revision), 1)
        self.assertEqual(shipment.original_expected_delivery_at, original_sla)
        self.assertEqual(shipment.expected_delivery_at, revised_sla)
        self.assertEqual(revision.shipment_id, shipment)
        self.assertEqual(revision.previous_expected_delivery_at, previous_sla)
        self.assertEqual(revision.new_expected_delivery_at, revised_sla)
        self.assertEqual(revision.reason, "Weather disruption")
        self.assertEqual(revision.changed_by_id, self.manager_user)
        self.assertGreaterEqual(revision.changed_at, before)
        self.assertLessEqual(revision.changed_at, after)

    def test_operator_cannot_revise_sla(self):
        shipment = self.create_shipment()
        shipment.action_assign(self.courier.id)
        previous_sla = shipment.expected_delivery_at

        with self.assertRaises(AccessError):
            shipment.with_user(self.operator_user).action_revise_sla(
                previous_sla + timedelta(hours=2),
                "Traffic disruption",
            )

        self.assertEqual(shipment.expected_delivery_at, previous_sla)
        self.assertFalse(shipment.sla_revision_ids)

    def test_delivery_delay_uses_current_and_original_sla(self):
        original_sla = fields.Datetime.now() - timedelta(hours=12)
        shipment = self.create_shipment(expected_delivery_at=original_sla)
        shipment.action_assign(self.courier.id)
        revised_sla = original_sla + timedelta(hours=6)
        shipment.with_user(self.manager_user).action_revise_sla(
            revised_sla,
            "Approved SLA extension",
        )
        shipment.action_record_pickup(shipment.package_ids.ids)
        shipment.action_start_transit()

        shipment.action_record_delivery(
            shipment.package_ids.ids,
            recipient_name="Receiving Clerk",
        )

        delivered_at = shipment.package_ids.delivery_event_id.occurred_at
        self.assertEqual(shipment.delivered_at, delivered_at)
        self.assertAlmostEqual(
            shipment.delay_hours,
            (delivered_at - revised_sla).total_seconds() / 3600.0,
        )
        self.assertAlmostEqual(
            shipment.original_delay_hours,
            (delivered_at - original_sla).total_seconds() / 3600.0,
        )

    def test_active_route_corrections_recompute_zones_and_warning(self):
        alternate_zone = self.env["parcel.delivery.zone"].create(
            {
                "name": "Barcelona Revision Zone",
                "company_id": self.company.id,
                "default_sla_hours": 30.0,
            }
        )
        self.env["parcel.zone.postcode.rule"].create(
            {
                "zone_id": alternate_zone.id,
                "country_id": self.country.id,
                "postcode_prefix": "08",
            }
        )
        self.courier.write({"zone_ids": [Command.link(alternate_zone.id)]})
        shipment = self.create_shipment()
        shipment.action_assign(self.courier.id)
        original_zone = shipment.destination_zone_id

        shipment.with_user(self.manager_user).action_correct_route(
            {
                "delivery_zip": "08001",
                "delivery_country_id": self.country.id,
            },
            "Corrected destination postcode",
        )

        first_correction = shipment.route_correction_ids
        self.assertEqual(len(first_correction), 1)
        self.assertTrue(first_correction.applied)
        self.assertEqual(first_correction.previous_destination_zone_id, original_zone)
        self.assertEqual(first_correction.new_destination_zone_id, alternate_zone)
        self.assertEqual(first_correction.previous_values["delivery_zip"], "28080")
        self.assertEqual(first_correction.new_values["delivery_zip"], "08001")
        self.assertEqual(first_correction.changed_by_id, self.manager_user)
        self.assertEqual(first_correction.reason, "Corrected destination postcode")
        self.assertEqual(shipment.destination_zone_id, alternate_zone)
        self.assertFalse(shipment.coverage_warning)

        shipment.with_user(self.manager_user).action_correct_route(
            {"delivery_zip": "99992"},
            "Destination outside configured coverage",
        )

        corrections = shipment.route_correction_ids.sorted("id")
        second_correction = corrections[1]
        self.assertEqual(len(corrections), 2)
        self.assertTrue(second_correction.applied)
        self.assertEqual(
            second_correction.previous_destination_zone_id,
            alternate_zone,
        )
        self.assertFalse(second_correction.new_destination_zone_id)
        self.assertEqual(shipment.delivery_zip, "99992")
        self.assertFalse(shipment.destination_zone_id)
        self.assertTrue(shipment.coverage_warning)

    def test_terminal_route_correction_is_annotation_only(self):
        shipment = self.create_shipment()
        shipment.with_user(self.manager_user).action_cancel(
            "Customer cancelled before pickup"
        )
        route_snapshot = (
            shipment.pickup_zip,
            shipment.pickup_country_id,
            shipment.delivery_zip,
            shipment.delivery_country_id,
            shipment.origin_zone_id,
            shipment.destination_zone_id,
            shipment.coverage_warning,
        )

        shipment.with_user(self.manager_user).action_correct_route(
            {"delivery_zip": "99992"},
            "Post-cancellation address annotation",
        )

        correction = shipment.route_correction_ids
        self.assertEqual(len(correction), 1)
        self.assertFalse(correction.applied)
        self.assertEqual(correction.new_values["delivery_zip"], "99992")
        self.assertEqual(correction.reason, "Post-cancellation address annotation")
        self.assertEqual(correction.changed_by_id, self.manager_user)
        self.assertEqual(
            (
                shipment.pickup_zip,
                shipment.pickup_country_id,
                shipment.delivery_zip,
                shipment.delivery_country_id,
                shipment.origin_zone_id,
                shipment.destination_zone_id,
                shipment.coverage_warning,
            ),
            route_snapshot,
        )

    def test_manager_reassigns_live_shipment_with_reason_and_releases_capacity(self):
        original_courier = self.create_courier(max_concurrent_shipments=1)
        replacement_courier = self.create_courier(max_concurrent_shipments=1)
        shipment = self.create_shipment()
        waiting = self.create_shipment()
        shipment.action_assign(original_courier.id)
        shipment.action_record_pickup(shipment.package_ids.ids)
        shipment.action_start_transit()

        with self.assertRaises(AccessError):
            shipment.with_user(self.operator_user).action_reassign(
                replacement_courier.id,
                "Courier unavailable",
            )
        with self.assertRaises(UserError):
            shipment.with_user(self.manager_user).action_reassign(
                replacement_courier.id,
                "",
            )

        shipment.with_user(self.manager_user).action_reassign(
            replacement_courier.id,
            "Courier unavailable",
        )

        self.assertEqual(shipment.courier_id, replacement_courier)
        self.assertEqual(shipment.state, "in_transit")
        waiting.action_assign(original_courier.id)
        self.assertEqual(waiting.courier_id, original_courier)
        self.assertEqual(waiting.state, "assigned")

    def test_reassignment_history_is_exact_append_only_and_skips_noop(self):
        original_courier = self.create_courier()
        replacement_courier = self.create_courier()
        shipment = self.create_shipment()
        shipment.action_assign(original_courier.id)
        shipment.action_record_pickup(shipment.package_ids.ids)
        before = fields.Datetime.now()

        shipment.with_user(self.manager_user).action_reassign(
            replacement_courier.id,
            "  Courier vehicle breakdown  ",
        )

        after = fields.Datetime.now()
        history = shipment.courier_reassignment_ids
        self.assertEqual(len(history), 1)
        self.assertEqual(history.shipment_id, shipment)
        self.assertEqual(history.previous_courier_id, original_courier)
        self.assertEqual(history.new_courier_id, replacement_courier)
        self.assertEqual(history.reason, "Courier vehicle breakdown")
        self.assertEqual(history.changed_by_id, self.manager_user)
        self.assertEqual(history.company_id, self.company)
        self.assertGreaterEqual(history.occurred_at, before)
        self.assertLessEqual(history.occurred_at, after)

        shipment.with_user(self.manager_user).action_reassign(
            replacement_courier.id,
            "No courier change",
        )

        self.assertEqual(len(shipment.courier_reassignment_ids), 1)
        with self.assertRaises(AccessError):
            history.write({"reason": "Rewritten reason"})
        with self.assertRaises(AccessError):
            history.unlink()
        with self.assertRaises(AccessError):
            self.env["parcel.courier.reassignment"].with_user(self.manager_user).create(
                {
                    "shipment_id": shipment.id,
                    "previous_courier_id": original_courier.id,
                    "new_courier_id": replacement_courier.id,
                    "reason": "Forged history",
                }
            )

    def test_terminal_route_wizard_reports_audit_only_completion(self):
        shipment = self.create_shipment()
        shipment.with_user(self.manager_user).action_cancel(
            "Customer cancelled before pickup"
        )
        route_snapshot = (
            shipment.delivery_zip,
            shipment.destination_zone_id,
            shipment.coverage_warning,
        )
        wizard = (
            self.env["parcel.route.wizard"]
            .with_user(self.manager_user)
            .with_context(
                active_model="parcel.shipment",
                active_id=shipment.id,
            )
            .create(
                {
                    "shipment_id": shipment.id,
                    "delivery_zip": "99993",
                    "reason": "Post-cancellation route annotation",
                }
            )
        )

        result = wizard.action_confirm()

        self.assertEqual(wizard.shipment_state, "cancelled")
        self.assertEqual(result["tag"], "display_notification")
        self.assertEqual(result["params"]["type"], "warning")
        self.assertIn("audit only", result["params"]["message"])
        self.assertEqual(
            (
                shipment.delivery_zip,
                shipment.destination_zone_id,
                shipment.coverage_warning,
            ),
            route_snapshot,
        )
        self.assertFalse(shipment.route_correction_ids.applied)

    def test_route_wizard_view_distinguishes_apply_from_audit_only(self):
        arch = etree.fromstring(
            self.env.ref(
                "parcel_transport_management.view_ptm_route_wizard_form"
            ).arch_db
        )

        warning = arch.xpath(
            "//*[@role='alert' and @invisible=\"shipment_state not in ['delivered', 'cancelled']\"]"
        )
        self.assertEqual(len(warning), 1)
        self.assertIn("audit only", " ".join(warning[0].itertext()).lower())
        apply_button = arch.xpath("//button[@string='Update Route Snapshot']")
        audit_button = arch.xpath("//button[@string='Record Audit Annotation']")
        self.assertEqual(len(apply_button), 1)
        self.assertEqual(len(audit_button), 1)

    def test_failed_live_reassignment_rolls_back_capacity_reservation(self):
        original_courier = self.create_courier(max_concurrent_shipments=1)
        full_courier = self.create_courier(max_concurrent_shipments=1)
        shipment = self.create_shipment()
        blocker = self.create_shipment()
        waiting = self.create_shipment()
        shipment.action_assign(original_courier.id)
        blocker.action_assign(full_courier.id)

        with self.assertRaises(UserError):
            shipment.with_user(self.manager_user).action_reassign(
                full_courier.id,
                "Attempted operational reassignment",
            )

        self.assertEqual(shipment.courier_id, original_courier)
        self.assertFalse(shipment.courier_reassignment_ids)
        self.assertEqual(shipment.state, "assigned")
        with self.assertRaises(UserError):
            waiting.action_assign(original_courier.id)
        self.assertFalse(waiting.courier_id)
        self.assertEqual(waiting.state, "draft")
