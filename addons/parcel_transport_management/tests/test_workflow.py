from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ParcelTestCase


@tagged("post_install", "-at_install")
class TestParcelWorkflow(ParcelTestCase):
    def _shipment_with_weights(self, *weights, uom=None, **overrides):
        shipment = self.create_shipment(**overrides)
        first_package = shipment.package_ids
        first_package.write(
            {
                "weight": weights[0],
                "weight_uom_id": (uom or self.kg_uom).id,
            }
        )
        for weight in weights[1:]:
            self.create_package(shipment, weight=weight, uom=uom or self.kg_uom)
        return shipment

    def _start_transit(self, shipment, courier=None):
        courier = courier or self.create_courier()
        shipment.action_assign(courier.id)
        shipment.action_record_pickup(shipment.package_ids.ids)
        shipment.action_start_transit()
        return courier

    def test_transit_requires_an_assigned_courier(self):
        shipment = self.create_shipment()

        with self.assertRaises(UserError):
            shipment.action_start_transit()

        self.assertEqual(shipment.state, "draft")
        self.assertFalse(shipment.courier_id)

    def test_assign_and_unassign_courier(self):
        shipment = self.create_shipment()
        courier = self.create_courier()

        shipment.action_assign(courier.id)

        self.assertEqual(shipment.courier_id, courier)
        self.assertEqual(shipment.state, "assigned")

        shipment.action_unassign()

        self.assertFalse(shipment.courier_id)
        self.assertEqual(shipment.state, "draft")

    def test_assignment_enforces_concurrent_shipment_capacity(self):
        courier = self.create_courier(
            max_concurrent_shipments=1,
            max_concurrent_weight=100.0,
            max_weight_uom_id=self.kg_uom.id,
        )
        assigned = self.create_shipment()
        rejected = self.create_shipment()
        assigned.action_assign(courier.id)

        with self.assertRaises(UserError):
            rejected.action_assign(courier.id)

        self.assertEqual(assigned.courier_id, courier)
        self.assertFalse(rejected.courier_id)
        self.assertEqual(rejected.state, "draft")

    def test_assignment_enforces_combined_weight_capacity(self):
        courier = self.create_courier(
            max_concurrent_shipments=10,
            max_concurrent_weight=10.0,
            max_weight_uom_id=self.kg_uom.id,
        )
        assigned = self._shipment_with_weights(6.0)
        rejected = self._shipment_with_weights(5.0)
        assigned.action_assign(courier.id)

        with self.assertRaises(UserError):
            rejected.action_assign(courier.id)

        self.assertEqual(assigned.courier_id, courier)
        self.assertFalse(rejected.courier_id)

    def test_assignment_converts_weight_before_checking_capacity(self):
        courier = self.create_courier(
            max_concurrent_shipments=10,
            max_concurrent_weight=10.0,
            max_weight_uom_id=self.lb_uom.id,
        )
        shipment = self._shipment_with_weights(5.0, uom=self.kg_uom)

        with self.assertRaises(UserError):
            shipment.action_assign(courier.id)

        self.assertFalse(shipment.courier_id)

    def test_unavailable_courier_cannot_be_assigned(self):
        courier = self.create_courier(availability="unavailable")
        shipment = self.create_shipment()

        with self.assertRaises(UserError):
            shipment.action_assign(courier.id)

        self.assertFalse(shipment.courier_id)
        self.assertEqual(shipment.state, "draft")

    def test_archived_courier_cannot_be_assigned(self):
        courier = self.create_courier()
        courier.active = False
        shipment = self.create_shipment()

        with self.assertRaises(UserError):
            shipment.action_assign(courier.id)

        self.assertFalse(shipment.courier_id)
        self.assertEqual(shipment.state, "draft")

    def test_archived_courier_cannot_receive_reassignment(self):
        original_courier = self.create_courier()
        archived_courier = self.create_courier()
        shipment = self.create_shipment()
        shipment.action_assign(original_courier.id)
        archived_courier.active = False

        with self.assertRaises(UserError):
            shipment.action_reassign(archived_courier.id)

        self.assertEqual(shipment.courier_id, original_courier)
        self.assertFalse(shipment.courier_reassignment_ids)

    def test_only_draft_shipments_can_be_deleted(self):
        draft = self.create_shipment()
        assigned = self.create_shipment()
        cancelled = self.create_shipment()
        assigned.action_assign(self.create_courier().id)
        cancelled.action_cancel("Customer cancelled before pickup")
        assigned_package = assigned.package_ids
        cancelled_package = cancelled.package_ids

        draft.unlink()

        self.assertFalse(draft.exists())
        with self.assertRaises(UserError):
            assigned.unlink()
        with self.assertRaises(UserError):
            cancelled.unlink()
        self.assertTrue(assigned.exists())
        self.assertTrue(cancelled.exists())
        self.assertTrue(assigned_package.exists())
        self.assertTrue(cancelled_package.exists())

    def test_partial_pickup_records_only_selected_package(self):
        shipment = self._shipment_with_weights(1.0, 2.0)
        first_package, pending_package = shipment.package_ids.sorted("id")
        shipment.action_assign(self.create_courier().id)

        shipment.action_record_pickup(first_package.ids, note="First parcel collected")

        self.assertEqual(shipment.state, "partially_picked_up")
        self.assertTrue(first_package.pickup_event_id)
        self.assertFalse(pending_package.pickup_event_id)

    def test_transit_rejects_incomplete_pickup(self):
        shipment = self._shipment_with_weights(1.0, 2.0)
        first_package, pending_package = shipment.package_ids.sorted("id")
        shipment.action_assign(self.create_courier().id)
        shipment.action_record_pickup(first_package.ids)

        with self.assertRaises(UserError):
            shipment.action_start_transit()

        self.assertEqual(shipment.state, "partially_picked_up")
        self.assertTrue(first_package.pickup_event_id)
        self.assertFalse(pending_package.pickup_event_id)

    def test_full_pickup_allows_transit(self):
        shipment = self._shipment_with_weights(1.0, 2.0)
        shipment.action_assign(self.create_courier().id)

        shipment.action_record_pickup(shipment.package_ids.ids)
        shipment.action_start_transit()

        self.assertEqual(shipment.state, "in_transit")
        self.assertTrue(all(shipment.package_ids.mapped("pickup_event_id")))

    def test_partial_then_final_delivery(self):
        shipment = self._shipment_with_weights(1.0, 2.0)
        self._start_transit(shipment)
        first_package, pending_package = shipment.package_ids.sorted("id")

        shipment.action_record_delivery(
            first_package.ids,
            recipient_name="Receiving Clerk",
            note="First parcel delivered",
        )

        self.assertEqual(shipment.state, "partially_delivered")
        self.assertTrue(first_package.delivery_event_id)
        self.assertFalse(pending_package.delivery_event_id)

        shipment.action_record_delivery(
            pending_package.ids,
            recipient_name="Receiving Clerk",
        )

        self.assertEqual(shipment.state, "delivered")
        self.assertTrue(all(shipment.package_ids.mapped("delivery_event_id")))

    def test_in_transit_failure_releases_capacity_and_retry_restores_transit(self):
        courier = self.create_courier(
            max_concurrent_shipments=1,
            max_concurrent_weight=1.0,
            max_weight_uom_id=self.kg_uom.id,
        )
        shipment = self._shipment_with_weights(1.0)
        self._start_transit(shipment, courier=courier)

        self.assertEqual(courier.current_shipment_count, 1)
        self.assertEqual(courier.current_weight, 1.0)

        attempt = shipment.action_record_delivery_failure(
            "Vehicle could not complete the route"
        )

        self.assertEqual(shipment.state, "delivery_failed")
        self.assertFalse(shipment.courier_id)
        self.assertEqual(courier.current_shipment_count, 0)
        self.assertEqual(courier.current_weight, 0.0)
        self.assertEqual(attempt.shipment_id, shipment)
        self.assertEqual(attempt.company_id, shipment.company_id)
        self.assertEqual(attempt.courier_id, courier)
        self.assertEqual(attempt.confirmed_by_id, self.env.user)
        self.assertEqual(attempt.package_ids, shipment.package_ids)
        self.assertTrue(attempt.occurred_at)

        retry = shipment.action_retry_delivery(
            courier.id,
            "Vehicle is available after inspection",
        )

        self.assertEqual(shipment.state, "in_transit")
        self.assertEqual(shipment.courier_id, courier)
        self.assertEqual(courier.current_shipment_count, 1)
        self.assertEqual(courier.current_weight, 1.0)
        self.assertEqual(retry.attempt_id, attempt)
        self.assertEqual(retry.shipment_id, shipment)
        self.assertEqual(retry.previous_courier_id, courier)
        self.assertEqual(retry.new_courier_id, courier)
        self.assertEqual(retry.dispatched_by_id, self.env.user)
        self.assertTrue(retry.occurred_at)

    def test_partial_delivery_failure_preserves_delivery_and_retry_state(self):
        original_courier = self.create_courier()
        retry_courier = self.create_courier(name="Retry Courier")
        shipment = self._shipment_with_weights(1.0, 2.0)
        self._start_transit(shipment, courier=original_courier)
        delivered_package, pending_package = shipment.package_ids.sorted("id")
        delivery = shipment.action_record_delivery(
            delivered_package.ids,
            recipient_name="Receiving Clerk",
        )

        attempt = shipment.action_record_delivery_failure(
            "The remaining parcel could not be delivered"
        )

        self.assertEqual(shipment.state, "delivery_failed")
        self.assertEqual(delivered_package.delivery_event_id, delivery)
        self.assertFalse(pending_package.delivery_event_id)
        self.assertEqual(attempt.package_ids, pending_package)

        retry = shipment.action_retry_delivery(
            retry_courier.id,
            "Recipient confirmed a new delivery window",
        )

        self.assertEqual(shipment.state, "partially_delivered")
        self.assertEqual(shipment.courier_id, retry_courier)
        self.assertEqual(delivered_package.delivery_event_id, delivery)
        self.assertFalse(pending_package.delivery_event_id)
        self.assertEqual(retry.attempt_id, attempt)
        self.assertEqual(retry.previous_courier_id, original_courier)
        self.assertEqual(retry.new_courier_id, retry_courier)

    def test_failure_rejects_blank_reason_and_invalid_state_atomically(self):
        in_transit = self._shipment_with_weights(1.0)
        in_transit_courier = self._start_transit(in_transit)

        with self.assertRaises(UserError):
            in_transit.action_record_delivery_failure(" \t ")

        self.assertEqual(in_transit.state, "in_transit")
        self.assertEqual(in_transit.courier_id, in_transit_courier)
        self.assertFalse(in_transit.delivery_attempt_ids)

        assigned = self._shipment_with_weights(1.0)
        assigned_courier = self.create_courier()
        assigned.action_assign(assigned_courier.id)

        with self.assertRaises(UserError):
            assigned.action_record_delivery_failure(
                "Failure cannot be recorded before transit"
            )

        self.assertEqual(assigned.state, "assigned")
        self.assertEqual(assigned.courier_id, assigned_courier)
        self.assertFalse(assigned.delivery_attempt_ids)

    def test_retry_rejects_full_courier_atomically(self):
        failed = self._shipment_with_weights(1.0)
        self._start_transit(failed)
        attempt = failed.action_record_delivery_failure("Access to recipient blocked")
        full_courier = self.create_courier(
            max_concurrent_shipments=1,
            max_concurrent_weight=1.0,
            max_weight_uom_id=self.kg_uom.id,
        )
        occupying = self._shipment_with_weights(1.0)
        occupying.action_assign(full_courier.id)

        with self.assertRaises(UserError):
            failed.action_retry_delivery(
                full_courier.id,
                "Dispatch to another courier",
            )

        self.assertEqual(failed.state, "delivery_failed")
        self.assertFalse(failed.courier_id)
        self.assertFalse(attempt.retry_ids)
        self.assertFalse(failed.delivery_retry_ids)
        self.assertEqual(occupying.courier_id, full_courier)
        self.assertEqual(full_courier.current_shipment_count, 1)
