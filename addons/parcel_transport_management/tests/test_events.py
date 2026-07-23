from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import ParcelTestCase


@tagged("post_install", "-at_install")
class TestParcelEvents(ParcelTestCase):
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
                    "name": "PTM Event Manager",
                    "login": "ptm-event-manager-test",
                    "email": "ptm-event-manager@example.test",
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
                    "name": "PTM Event Operator",
                    "login": "ptm-event-operator-test",
                    "email": "ptm-event-operator@example.test",
                    "company_id": cls.company.id,
                    "company_ids": [Command.set(cls.company.ids)],
                    "group_ids": [Command.set((internal_group | operator_group).ids)],
                }
            )
        )

    def _start_transit(self, shipment, courier=None):
        shipment.action_assign((courier or self.create_courier()).id)
        shipment.action_record_pickup(shipment.package_ids.ids)
        shipment.action_start_transit()

    def test_event_timestamps_are_server_generated_and_actor_is_calling_user(self):
        shipment = self.create_shipment()
        shipment.action_assign(self.courier.id)
        before_pickup = fields.Datetime.now()

        shipment.with_user(self.operator_user).action_record_pickup(
            shipment.package_ids.ids,
            note="Loaded at origin desk",
        )

        after_pickup = fields.Datetime.now()
        pickup_event = shipment.package_ids.pickup_event_id
        self.assertGreaterEqual(pickup_event.occurred_at, before_pickup)
        self.assertLessEqual(pickup_event.occurred_at, after_pickup)
        self.assertEqual(pickup_event.confirmed_by_id, self.operator_user)
        self.assertEqual(pickup_event.note, "Loaded at origin desk")
        shipment.with_user(self.operator_user).action_start_transit()
        before_delivery = fields.Datetime.now()

        shipment.with_user(self.operator_user).action_record_delivery(
            shipment.package_ids.ids,
            recipient_name="Receiving Clerk",
            note="Identity checked at destination",
        )

        after_delivery = fields.Datetime.now()
        delivery_event = shipment.package_ids.delivery_event_id
        self.assertGreaterEqual(delivery_event.occurred_at, before_delivery)
        self.assertLessEqual(delivery_event.occurred_at, after_delivery)
        self.assertEqual(delivery_event.confirmed_by_id, self.operator_user)
        self.assertEqual(delivery_event.note, "Identity checked at destination")
        self.assertEqual(delivery_event.recipient_name, "Receiving Clerk")

    def test_partial_events_have_meaningful_distinct_display_names(self):
        shipment = self.create_shipment(
            packages=[
                {"weight": 1.0, "weight_uom_id": self.kg_uom.id},
                {"weight": 2.0, "weight_uom_id": self.kg_uom.id},
            ]
        )
        first_package, second_package = shipment.package_ids.sorted("id")
        shipment.action_assign(self.courier.id)
        shipment.action_record_pickup(first_package.ids)
        shipment.action_record_pickup(second_package.ids)
        shipment.action_start_transit()
        shipment.action_record_delivery(
            first_package.ids,
            recipient_name="Receiving Clerk",
        )
        shipment.action_record_delivery(
            second_package.ids,
            recipient_name="Receiving Clerk",
        )

        event_sets = (
            shipment.package_ids.pickup_event_id,
            shipment.package_ids.delivery_event_id,
        )
        for events in event_sets:
            self.assertEqual(len(events), 2)
            self.assertEqual(len(set(events.mapped("display_name"))), 2)
            for event in events:
                self.assertIn(shipment.reference, event.display_name)
                self.assertIn(
                    fields.Datetime.to_string(event.occurred_at),
                    event.display_name,
                )
                self.assertNotEqual(
                    event.display_name,
                    f"{event._name},{event.id}",
                )

    def test_pickup_event_cannot_be_written_or_unlinked_even_by_manager(self):
        shipment = self.create_shipment()
        shipment.action_assign(self.courier.id)
        shipment.action_record_pickup(shipment.package_ids.ids)
        event = shipment.package_ids.pickup_event_id.with_user(self.manager_user)

        with self.assertRaises(AccessError):
            event.write({"note": "Retrospective edit"})
        with self.assertRaises(AccessError):
            event.unlink()

        self.assertTrue(event.exists())
        self.assertFalse(event.note)
        self.assertEqual(shipment.package_ids.pickup_event_id, event)

    def test_delivery_event_cannot_be_written_or_unlinked_even_by_manager(self):
        shipment = self.create_shipment()
        self._start_transit(shipment)
        shipment.action_record_delivery(
            shipment.package_ids.ids,
            recipient_name="Receiving Clerk",
        )
        event = shipment.package_ids.delivery_event_id.with_user(self.manager_user)

        with self.assertRaises(AccessError):
            event.write({"recipient_name": "Different Recipient"})
        with self.assertRaises(AccessError):
            event.unlink()

        self.assertTrue(event.exists())
        self.assertEqual(event.recipient_name, "Receiving Clerk")
        self.assertEqual(shipment.package_ids.delivery_event_id, event)

    def test_duplicate_pickup_is_rejected_without_creating_another_event(self):
        shipment = self.create_shipment()
        shipment.action_assign(self.courier.id)
        package = shipment.package_ids
        shipment.action_record_pickup(package.ids)
        first_event = package.pickup_event_id

        with self.assertRaises(UserError):
            shipment.action_record_pickup(package.ids)

        self.assertEqual(package.pickup_event_id, first_event)
        self.assertEqual(
            self.env["parcel.pickup.event"].search_count(
                [("shipment_id", "=", shipment.id)]
            ),
            1,
        )
        self.assertEqual(shipment.state, "picked_up")

    def test_duplicate_delivery_is_rejected_without_creating_another_event(self):
        shipment = self.create_shipment()
        self._start_transit(shipment)
        package = shipment.package_ids
        shipment.action_record_delivery(
            package.ids,
            recipient_name="Receiving Clerk",
        )
        first_event = package.delivery_event_id

        with self.assertRaises(UserError):
            shipment.action_record_delivery(
                package.ids,
                recipient_name="Receiving Clerk",
            )

        self.assertEqual(package.delivery_event_id, first_event)
        self.assertEqual(
            self.env["parcel.delivery.event"].search_count(
                [("shipment_id", "=", shipment.id)]
            ),
            1,
        )
        self.assertEqual(shipment.state, "delivered")

    def test_delivery_without_pickup_is_rejected_without_an_event(self):
        shipment = self.create_shipment()
        shipment.action_assign(self.courier.id)

        with self.assertRaises(UserError):
            shipment.action_record_delivery(
                shipment.package_ids.ids,
                recipient_name="Receiving Clerk",
            )

        self.assertFalse(shipment.package_ids.delivery_event_id)
        self.assertEqual(
            self.env["parcel.delivery.event"].search_count(
                [("shipment_id", "=", shipment.id)]
            ),
            0,
        )
        self.assertEqual(shipment.state, "assigned")

    def test_capacity_remains_reserved_until_final_delivery(self):
        courier = self.create_courier(
            max_concurrent_shipments=1,
            max_concurrent_weight=100.0,
        )
        shipment = self.create_shipment(
            packages=[
                {"weight": 1.0, "weight_uom_id": self.kg_uom.id},
                {"weight": 2.0, "weight_uom_id": self.kg_uom.id},
            ]
        )
        waiting = self.create_shipment()
        shipment.action_assign(courier.id)
        shipment.action_record_pickup(shipment.package_ids.ids)
        shipment.action_start_transit()
        first_package, final_package = shipment.package_ids.sorted("id")

        shipment.action_record_delivery(
            first_package.ids,
            recipient_name="Receiving Clerk",
        )

        self.assertEqual(shipment.state, "partially_delivered")
        with self.assertRaises(UserError):
            waiting.action_assign(courier.id)
        self.assertFalse(waiting.courier_id)
        self.assertEqual(waiting.state, "draft")

        shipment.action_record_delivery(
            final_package.ids,
            recipient_name="Receiving Clerk",
        )
        waiting.action_assign(courier.id)

        self.assertEqual(shipment.state, "delivered")
        self.assertEqual(waiting.courier_id, courier)
        self.assertEqual(waiting.state, "assigned")
