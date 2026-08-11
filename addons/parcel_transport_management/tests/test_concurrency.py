import threading
import time
import uuid

from odoo import SUPERUSER_ID, Command, api
from odoo.exceptions import UserError
from odoo.orm.environments import Transaction
from odoo.tests import tagged
from psycopg2.errors import SerializationFailure

from .common import ParcelTestCase


@tagged("post_install", "-at_install")
class TestParcelConcurrency(ParcelTestCase):
    _BARRIER_TIMEOUT = 5.0
    _OVERLAP_TIMEOUT = 2.0
    _RACE_TIMEOUT = 10.0

    def _independent_env(self, cr, company_id, registry):
        if cr.transaction is None:
            cr.transaction = Transaction(registry)
        return api.Environment(
            cr,
            SUPERUSER_ID,
            {"allowed_company_ids": [company_id]},
        )

    def _create_scenario(
        self,
        package_weights_by_shipment,
        *,
        max_concurrent_shipments,
        max_concurrent_weight,
    ):
        dbname = self.env.cr.dbname
        company_id = self.company.id
        country_id = self.country.id
        kg_uom_id = self.kg_uom.id
        token = uuid.uuid4().hex
        postcode_prefix = str(uuid.uuid4().int)[:12]

        with self.env.registry.cursor() as cr:
            env = self._independent_env(cr, company_id, self.env.registry)
            sender = env["res.partner"].create(
                {
                    "name": f"Concurrency Sender {token}",
                    "company_id": company_id,
                    "street": "Concurrency Street 1",
                    "city": "Madrid",
                    "zip": postcode_prefix,
                    "country_id": country_id,
                }
            )
            recipient = env["res.partner"].create(
                {
                    "name": f"Concurrency Recipient {token}",
                    "company_id": company_id,
                    "street": "Concurrency Street 2",
                    "city": "Madrid",
                    "zip": postcode_prefix,
                    "country_id": country_id,
                }
            )
            zone = env["parcel.delivery.zone"].create(
                {
                    "name": f"Concurrency Zone {token}",
                    "company_id": company_id,
                    "default_sla_hours": 24.0,
                }
            )
            zone_rule = env["parcel.zone.postcode.rule"].create(
                {
                    "zone_id": zone.id,
                    "country_id": country_id,
                    "postcode_prefix": postcode_prefix,
                }
            )
            courier = env["parcel.courier"].create(
                {
                    "name": f"Concurrency Courier {token}",
                    "company_id": company_id,
                    "availability": "available",
                    "max_concurrent_shipments": max_concurrent_shipments,
                    "max_concurrent_weight": max_concurrent_weight,
                    "max_weight_uom_id": kg_uom_id,
                    "zone_ids": [Command.set(zone.ids)],
                }
            )
            shipments = env["parcel.shipment"]
            package_ids_by_shipment = []
            for weights in package_weights_by_shipment:
                shipment = env["parcel.shipment"].create(
                    {
                        "company_id": company_id,
                        "sender_id": sender.id,
                        "recipient_id": recipient.id,
                        "package_ids": [
                            Command.create(
                                {
                                    "weight": weight,
                                    "weight_uom_id": kg_uom_id,
                                }
                            )
                            for weight in weights
                        ],
                    }
                )
                shipments |= shipment
                package_ids_by_shipment.append(
                    tuple(shipment.package_ids.sorted("id").ids)
                )
            scenario = {
                "dbname": dbname,
                "registry": self.env.registry,
                "company_id": company_id,
                "courier_id": courier.id,
                "shipment_ids": tuple(shipments.ids),
                "package_ids_by_shipment": tuple(package_ids_by_shipment),
                "partner_ids": (sender.id, recipient.id),
                "zone_id": zone.id,
                "zone_rule_id": zone_rule.id,
            }
            cr.commit()

        self.addCleanup(self._cleanup_scenario, scenario)
        return scenario

    def _cleanup_scenario(self, scenario):
        with scenario["registry"].cursor() as cr:
            env = self._independent_env(
                cr, scenario["company_id"], scenario["registry"]
            )
            cr.execute(
                "DELETE FROM parcel_delivery_retry WHERE shipment_id IN %s",
                [scenario["shipment_ids"]],
            )
            cr.execute(
                "DELETE FROM parcel_delivery_attempt WHERE shipment_id IN %s",
                [scenario["shipment_ids"]],
            )
            cr.execute(
                "DELETE FROM parcel_package WHERE shipment_id IN %s",
                [scenario["shipment_ids"]],
            )
            cr.execute(
                "DELETE FROM parcel_pickup_event WHERE shipment_id IN %s",
                [scenario["shipment_ids"]],
            )
            cr.execute(
                "DELETE FROM parcel_delivery_event WHERE shipment_id IN %s",
                [scenario["shipment_ids"]],
            )
            cr.execute(
                "DELETE FROM parcel_sla_revision WHERE shipment_id IN %s",
                [scenario["shipment_ids"]],
            )
            cr.execute(
                "DELETE FROM parcel_courier_reassignment WHERE shipment_id IN %s",
                [scenario["shipment_ids"]],
            )
            cr.execute(
                "DELETE FROM parcel_route_correction WHERE shipment_id IN %s",
                [scenario["shipment_ids"]],
            )
            cr.execute(
                "DELETE FROM parcel_shipment WHERE id IN %s",
                [scenario["shipment_ids"]],
            )
            env["parcel.courier"].browse(scenario["courier_id"]).exists().unlink()
            env["parcel.zone.postcode.rule"].browse(
                scenario["zone_rule_id"]
            ).exists().unlink()
            env["parcel.delivery.zone"].browse(scenario["zone_id"]).exists().unlink()
            env["res.partner"].browse(scenario["partner_ids"]).exists().unlink()
            cr.commit()

    def _run_race(self, scenario, operations, *, expected_successes=None):
        barrier = threading.Barrier(len(operations), timeout=self._BARRIER_TIMEOUT)
        first_applied = threading.Event()
        second_finished = threading.Event()
        outcomes = [None] * len(operations)

        def run(index, operation):
            try:
                with scenario["registry"].cursor() as cr:
                    cr.execute("SET LOCAL lock_timeout TO '5s'")
                    cr.execute("SET LOCAL statement_timeout TO '8s'")
                    env = self._independent_env(
                        cr, scenario["company_id"], scenario["registry"]
                    )
                    try:
                        barrier.wait()
                        if index == 0:
                            try:
                                operation(env)
                            finally:
                                first_applied.set()
                            # Keep the first transaction uncommitted while the
                            # second observes or waits for its database locks.
                            second_finished.wait(self._OVERLAP_TIMEOUT)
                        else:
                            if not first_applied.wait(self._BARRIER_TIMEOUT):
                                raise TimeoutError(
                                    "First concurrent operation did not start"
                                )
                            try:
                                operation(env)
                            finally:
                                second_finished.set()
                        cr.commit()
                    except BaseException:
                        cr.rollback()
                        raise
                outcomes[index] = (True, None, None)
            except BaseException as error:
                outcomes[index] = (False, error, error.__traceback__)

        threads = [
            threading.Thread(
                target=run,
                args=(index, operation),
                name=f"parcel-race-{index}",
                daemon=True,
            )
            for index, operation in enumerate(operations)
        ]
        for thread in threads:
            thread.start()

        deadline = time.monotonic() + self._RACE_TIMEOUT
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        alive = [thread.name for thread in threads if thread.is_alive()]
        if alive:
            barrier.abort()
            first_applied.set()
            second_finished.set()
            self.fail(f"Concurrent operations did not finish: {alive}")

        for outcome in outcomes:
            self.assertIsNotNone(outcome)
            succeeded, error, traceback = outcome
            if not succeeded and not isinstance(
                error, (UserError, SerializationFailure)
            ):
                raise error.with_traceback(traceback)

        success_count = sum(succeeded for succeeded, _error, _traceback in outcomes)
        if expected_successes is not None:
            self.assertEqual(success_count, expected_successes)
        return success_count

    def _read_assignments(self, scenario):
        with scenario["registry"].cursor() as cr:
            env = self._independent_env(
                cr, scenario["company_id"], scenario["registry"]
            )
            return tuple(
                (shipment.state, shipment.courier_id.id)
                for shipment in env["parcel.shipment"].browse(scenario["shipment_ids"])
            )

    def _prepare_in_transit(self, scenario, shipment_id):
        with scenario["registry"].cursor() as cr:
            env = self._independent_env(
                cr, scenario["company_id"], scenario["registry"]
            )
            shipment = env["parcel.shipment"].browse(shipment_id)
            shipment.action_assign(scenario["courier_id"])
            shipment.action_record_pickup(shipment.package_ids.ids)
            shipment.action_start_transit()
            cr.commit()

    def _prepare_failed_delivery(self, scenario, shipment_id):
        with scenario["registry"].cursor() as cr:
            env = self._independent_env(
                cr, scenario["company_id"], scenario["registry"]
            )
            shipment = env["parcel.shipment"].browse(shipment_id)
            shipment.action_assign(scenario["courier_id"])
            shipment.action_record_pickup(shipment.package_ids.ids)
            shipment.action_start_transit()
            shipment.action_record_delivery_failure("Concurrent failure setup")
            cr.commit()

    def _read_retry_state(self, scenario):
        with scenario["registry"].cursor() as cr:
            env = self._independent_env(
                cr, scenario["company_id"], scenario["registry"]
            )
            shipments = env["parcel.shipment"].browse(scenario["shipment_ids"])
            attempts = env["parcel.delivery.attempt"].search(
                [("shipment_id", "in", scenario["shipment_ids"])]
            )
            retries = env["parcel.delivery.retry"].search(
                [("shipment_id", "in", scenario["shipment_ids"])]
            )
            return {
                "assignments": tuple(
                    (shipment.state, shipment.courier_id.id) for shipment in shipments
                ),
                "attempt_count": len(attempts),
                "retry_count": len(retries),
                "unresolved_attempt_count": len(
                    attempts.filtered(lambda attempt: not attempt.retry_ids)
                ),
            }

    def _read_delivery_state(self, scenario, shipment_id, package_ids):
        with scenario["registry"].cursor() as cr:
            env = self._independent_env(
                cr, scenario["company_id"], scenario["registry"]
            )
            shipment = env["parcel.shipment"].browse(shipment_id)
            packages = env["parcel.package"].browse(package_ids)
            return {
                "state": shipment.state,
                "delivery_event_ids": tuple(packages.mapped("delivery_event_id").ids),
                "delivery_event_count": env["parcel.delivery.event"].search_count(
                    [("shipment_id", "=", shipment_id)]
                ),
            }

    def test_two_assignments_competing_for_last_slot_accept_exactly_one(self):
        scenario = self._create_scenario(
            ((1.0,), (1.0,)),
            max_concurrent_shipments=1,
            max_concurrent_weight=100.0,
        )
        first_id, second_id = scenario["shipment_ids"]

        self._run_race(
            scenario,
            (
                lambda env: (
                    env["parcel.shipment"]
                    .browse(first_id)
                    .action_assign(scenario["courier_id"])
                ),
                lambda env: (
                    env["parcel.shipment"]
                    .browse(second_id)
                    .action_assign(scenario["courier_id"])
                ),
            ),
            expected_successes=1,
        )

        assignments = self._read_assignments(scenario)
        self.assertEqual(
            sum(
                state == "assigned" and courier_id == scenario["courier_id"]
                for state, courier_id in assignments
            ),
            1,
        )
        self.assertEqual(
            sum(
                state == "draft" and not courier_id for state, courier_id in assignments
            ),
            1,
        )

    def test_two_assignments_competing_for_last_kg_accept_exactly_one(self):
        scenario = self._create_scenario(
            ((1.0,), (1.0,)),
            max_concurrent_shipments=10,
            max_concurrent_weight=1.0,
        )
        first_id, second_id = scenario["shipment_ids"]

        self._run_race(
            scenario,
            (
                lambda env: (
                    env["parcel.shipment"]
                    .browse(first_id)
                    .action_assign(scenario["courier_id"])
                ),
                lambda env: (
                    env["parcel.shipment"]
                    .browse(second_id)
                    .action_assign(scenario["courier_id"])
                ),
            ),
            expected_successes=1,
        )

        assignments = self._read_assignments(scenario)
        self.assertEqual(
            sum(
                state == "assigned" and courier_id == scenario["courier_id"]
                for state, courier_id in assignments
            ),
            1,
        )
        self.assertEqual(
            sum(
                state == "draft" and not courier_id for state, courier_id in assignments
            ),
            1,
        )

    def test_cancellation_and_first_delivery_cannot_both_commit(self):
        scenario = self._create_scenario(
            ((1.0, 1.0),),
            max_concurrent_shipments=1,
            max_concurrent_weight=10.0,
        )
        shipment_id = scenario["shipment_ids"][0]
        first_package_id, second_package_id = scenario["package_ids_by_shipment"][0]
        self._prepare_in_transit(scenario, shipment_id)

        self._run_race(
            scenario,
            (
                lambda env: (
                    env["parcel.shipment"]
                    .browse(shipment_id)
                    .action_cancel("Concurrent cancellation")
                ),
                lambda env: (
                    env["parcel.shipment"]
                    .browse(shipment_id)
                    .action_record_delivery(
                        [first_package_id], recipient_name="Receiving Clerk"
                    )
                ),
            ),
            expected_successes=1,
        )

        result = self._read_delivery_state(
            scenario, shipment_id, (first_package_id, second_package_id)
        )
        if result["state"] == "cancelled":
            self.assertFalse(result["delivery_event_ids"])
            self.assertEqual(result["delivery_event_count"], 0)
        else:
            self.assertEqual(result["state"], "partially_delivered")
            self.assertEqual(len(result["delivery_event_ids"]), 1)
            self.assertEqual(result["delivery_event_count"], 1)

    def test_two_deliveries_of_same_package_create_one_event(self):
        scenario = self._create_scenario(
            ((1.0,),),
            max_concurrent_shipments=1,
            max_concurrent_weight=10.0,
        )
        shipment_id = scenario["shipment_ids"][0]
        package_id = scenario["package_ids_by_shipment"][0][0]
        self._prepare_in_transit(scenario, shipment_id)
        before = self._read_delivery_state(scenario, shipment_id, (package_id,))
        self.assertEqual(before["delivery_event_count"], 0)

        success_count = self._run_race(
            scenario,
            (
                lambda env: (
                    env["parcel.shipment"]
                    .browse(shipment_id)
                    .action_record_delivery(
                        [package_id], recipient_name="Receiving Clerk"
                    )
                ),
                lambda env: (
                    env["parcel.shipment"]
                    .browse(shipment_id)
                    .action_record_delivery(
                        [package_id], recipient_name="Receiving Clerk"
                    )
                ),
            ),
        )

        self.assertGreaterEqual(success_count, 1)
        result = self._read_delivery_state(scenario, shipment_id, (package_id,))
        self.assertEqual(result["state"], "delivered")
        self.assertEqual(len(result["delivery_event_ids"]), 1)
        self.assertEqual(result["delivery_event_count"], 1)

    def test_two_failed_shipments_competing_for_retry_slot_commit_exactly_one(self):
        scenario = self._create_scenario(
            ((1.0,), (1.0,)),
            max_concurrent_shipments=1,
            max_concurrent_weight=100.0,
        )
        first_id, second_id = scenario["shipment_ids"]
        self._prepare_failed_delivery(scenario, first_id)
        self._prepare_failed_delivery(scenario, second_id)

        self._run_race(
            scenario,
            (
                lambda env: (
                    env["parcel.shipment"]
                    .browse(first_id)
                    .action_retry_delivery(
                        scenario["courier_id"],
                        "Concurrent retry dispatch",
                    )
                ),
                lambda env: (
                    env["parcel.shipment"]
                    .browse(second_id)
                    .action_retry_delivery(
                        scenario["courier_id"],
                        "Concurrent retry dispatch",
                    )
                ),
            ),
            expected_successes=1,
        )

        result = self._read_retry_state(scenario)
        self.assertEqual(result["attempt_count"], 2)
        self.assertEqual(result["retry_count"], 1)
        self.assertEqual(result["unresolved_attempt_count"], 1)
        self.assertEqual(
            sum(
                state == "in_transit" and courier_id == scenario["courier_id"]
                for state, courier_id in result["assignments"]
            ),
            1,
        )
        self.assertEqual(
            sum(
                state == "delivery_failed" and not courier_id
                for state, courier_id in result["assignments"]
            ),
            1,
        )
