import json
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import tagged

from .common import ParcelTestCase


@tagged("post_install", "-at_install")
class TestParcelDashboard(ParcelTestCase):
    NOW = datetime(2026, 4, 15, 12, 0, 0)

    ROOT_KEYS = {
        "stats",
        "shipments",
        "queue_total",
        "queue_truncated",
        "lanes",
        "lane_total",
        "lanes_truncated",
        "zone_pressure",
        "zone_pressure_total",
        "zone_pressure_truncated",
        "couriers",
        "courier_total",
        "couriers_truncated",
        "activity",
        "permissions",
        "generated_at",
    }
    STATS_KEYS = {
        "total_shipments",
        "reserved_shipments",
        "in_transit_shipments",
        "delayed_shipments",
        "partial_shipments",
        "delivered_today",
        "total_packages",
        "delivered_packages",
        "coverage_warnings",
    }
    SHIPMENT_KEYS = {
        "id",
        "reference",
        "state",
        "state_label",
        "expected_delivery_at",
        "delay_hours",
        "original_delay_hours",
        "coverage_warning",
        "coverage_warning_reason",
        "package_count",
        "picked_up_count",
        "delivered_count",
        "total_weight_kg",
        "courier",
        "origin_zone",
        "destination_zone",
    }
    ZONE_PRESSURE_KEYS = {
        "id",
        "name",
        "code",
        "active_shipments",
        "delayed_shipments",
        "package_count",
        "coverage_warnings",
        "archived",
    }
    LANE_KEYS = {
        "origin_zone",
        "destination_zone",
        "shipment_count",
        "delayed_shipments",
        "package_count",
        "coverage_warnings",
    }
    COURIER_KEYS = {
        "id",
        "name",
        "availability",
        "workload_state",
        "current_shipment_count",
        "current_weight",
        "max_concurrent_shipments",
        "max_concurrent_weight",
        "weight_uom",
    }
    ACTIVITY_KEYS = {
        "event_type",
        "occurred_at",
        "shipment_reference",
        "package_count",
    }

    def _dashboard(self, company=None, user=None, **context):
        company = company or self.company
        model = (
            self.env["parcel.shipment"]
            .with_company(company)
            .with_context(allowed_company_ids=[company.id], **context)
        )
        if user:
            model = model.with_user(user)
        with patch.object(fields.Datetime, "now", return_value=self.NOW):
            return model.get_dashboard_data()

    def _assign_at(self, shipment, courier, when):
        with patch.object(fields.Datetime, "now", return_value=when):
            shipment.action_assign(courier.id)

    def _set_event_time(self, event, occurred_at):
        self.env.cr.execute(
            f"UPDATE {event._table} SET occurred_at = %s WHERE id = %s",
            (occurred_at, event.id),
        )
        event.invalidate_recordset(["occurred_at"])

    def _create_uncovered_recipient(self, company=None, name="Uncovered Recipient"):
        company = company or self.company
        return self.env["res.partner"].create(
            {
                "name": name,
                "company_id": company.id,
                "street": "Outside Coverage 1",
                "city": "Seville",
                "zip": "41001",
                "country_id": self.country.id,
            }
        )

    def _create_user(self, name, group_xmlid):
        internal_group = self.env.ref("base.group_user")
        parcel_group = self.env.ref(group_xmlid)
        return (
            self.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": name,
                    "login": f"{name.lower().replace(' ', '-')}-dashboard-test",
                    "email": f"{name.lower().replace(' ', '-')}@example.test",
                    "company_id": self.company.id,
                    "company_ids": [Command.set(self.company.ids)],
                    "group_ids": [Command.set((internal_group | parcel_group).ids)],
                }
            )
        )

    def _complete_delivery(self, shipment, courier, delivered_at):
        self._assign_at(shipment, courier, delivered_at - timedelta(hours=1))
        pickup_event = shipment.action_record_pickup(shipment.package_ids.ids)
        shipment.action_start_transit()
        delivery_event = shipment.action_record_delivery(
            shipment.package_ids.ids,
            recipient_name="Receiving Clerk",
        )
        self._set_event_time(pickup_event, delivered_at - timedelta(minutes=30))
        self._set_event_time(delivery_event, delivered_at)
        shipment = delivery_event.shipment_id
        shipment.flush_recordset(["delivered_at"])
        self.env.cr.execute(
            f"UPDATE {shipment._table} SET delivered_at = %s WHERE id = %s",
            (delivered_at, shipment.id),
        )
        shipment.invalidate_recordset(["delivered_at"])
        shipment.modified(["delivered_at"])
        return delivery_event

    def test_dashboard_contract_counts_delays_deliveries_and_warnings(self):
        uncovered = self.create_shipment(
            recipient=self._create_uncovered_recipient(),
        )
        delayed = self.create_shipment(
            packages=[
                {"weight": 1.0, "weight_uom_id": self.kg_uom.id},
                {"weight": 2.0, "weight_uom_id": self.kg_uom.id},
            ],
        )
        self._assign_at(delayed, self.courier, self.NOW - timedelta(days=2))
        delayed.action_revise_sla(
            self.NOW - timedelta(hours=6),
            "Dashboard delay scenario",
        )

        partial = self.create_shipment(
            packages=[
                {"weight": 1.0, "weight_uom_id": self.kg_uom.id},
                {"weight": 1.0, "weight_uom_id": self.kg_uom.id},
            ]
        )
        self._assign_at(partial, self.courier, self.NOW)
        partial.action_record_pickup(partial.package_ids.sorted("id")[:1].ids)

        in_transit = self.create_shipment()
        self._assign_at(in_transit, self.courier, self.NOW)
        in_transit.action_record_pickup(in_transit.package_ids.ids)
        in_transit.action_start_transit()

        delivered_today = self.create_shipment(
            packages=[
                {"weight": 1.0, "weight_uom_id": self.kg_uom.id},
                {"weight": 1.0, "weight_uom_id": self.kg_uom.id},
            ]
        )
        self._complete_delivery(
            delivered_today,
            self.courier,
            self.NOW - timedelta(hours=2),
        )
        delivered_yesterday = self.create_shipment()
        self._complete_delivery(
            delivered_yesterday,
            self.courier,
            self.NOW - timedelta(days=1, hours=2),
        )

        dashboard = self._dashboard()

        json.dumps(dashboard)
        self.assertEqual(set(dashboard), self.ROOT_KEYS)
        self.assertEqual(set(dashboard["stats"]), self.STATS_KEYS)
        self.assertEqual(
            dashboard["stats"],
            {
                "total_shipments": 6,
                "reserved_shipments": 3,
                "in_transit_shipments": 1,
                "delayed_shipments": 1,
                "partial_shipments": 1,
                "delivered_today": 1,
                "total_packages": 9,
                "delivered_packages": 3,
                "coverage_warnings": 1,
            },
        )
        self.assertEqual(dashboard["generated_at"], "2026-04-15 12:00:00")
        self.assertEqual(dashboard["queue_total"], 4)
        self.assertFalse(dashboard["queue_truncated"])
        self.assertEqual(dashboard["zone_pressure_total"], 1)
        self.assertFalse(dashboard["zone_pressure_truncated"])
        self.assertEqual(dashboard["courier_total"], 1)
        self.assertFalse(dashboard["couriers_truncated"])
        self.assertEqual(
            dashboard["permissions"],
            {
                "can_create_shipments": False,
                "can_create_couriers": False,
                "can_manage_zones": False,
            },
        )
        self.assertTrue(dashboard["shipments"])
        self.assertTrue(dashboard["lanes"])
        self.assertTrue(dashboard["zone_pressure"])
        self.assertTrue(dashboard["couriers"])
        self.assertTrue(dashboard["activity"])
        for item in dashboard["shipments"]:
            self.assertEqual(set(item), self.SHIPMENT_KEYS)
        for item in dashboard["lanes"]:
            self.assertEqual(set(item), self.LANE_KEYS)
        for item in dashboard["zone_pressure"]:
            self.assertEqual(set(item), self.ZONE_PRESSURE_KEYS)
        for item in dashboard["couriers"]:
            self.assertEqual(set(item), self.COURIER_KEYS)
        for item in dashboard["activity"]:
            self.assertEqual(set(item), self.ACTIVITY_KEYS)

        delayed_item = next(
            item for item in dashboard["shipments"] if item["id"] == delayed.id
        )
        self.assertEqual(
            delayed_item,
            {
                "id": delayed.id,
                "reference": delayed.reference,
                "state": "assigned",
                "state_label": "Assigned",
                "expected_delivery_at": "2026-04-15 06:00:00",
                "delay_hours": 6.0,
                "original_delay_hours": 24.0,
                "coverage_warning": False,
                "coverage_warning_reason": False,
                "package_count": 2,
                "picked_up_count": 0,
                "delivered_count": 0,
                "total_weight_kg": 3.0,
                "courier": {"id": self.courier.id, "name": self.courier.name},
                "origin_zone": {"id": self.zone.id, "name": self.zone.name},
                "destination_zone": {"id": self.zone.id, "name": self.zone.name},
            },
        )
        warning_item = next(
            item for item in dashboard["shipments"] if item["id"] == uncovered.id
        )
        self.assertTrue(warning_item["coverage_warning"])
        self.assertEqual(
            warning_item["coverage_warning_reason"], uncovered.coverage_warning
        )

    def test_lanes_zone_pressure_and_courier_capacity_are_aggregated(self):
        origin_zone = self.env["parcel.delivery.zone"].create(
            {
                "name": "Dashboard Origin",
                "code": "ORG",
                "company_id": self.company.id,
                "default_sla_hours": 24.0,
            }
        )
        destination_zone = self.env["parcel.delivery.zone"].create(
            {
                "name": "Dashboard Destination",
                "code": "DST",
                "company_id": self.company.id,
                "default_sla_hours": 24.0,
            }
        )
        self.env["parcel.zone.postcode.rule"].create(
            [
                {
                    "zone_id": origin_zone.id,
                    "country_id": self.country.id,
                    "postcode_prefix": "28013",
                },
                {
                    "zone_id": destination_zone.id,
                    "country_id": self.country.id,
                    "postcode_prefix": "28080",
                },
            ]
        )
        courier = self.create_courier(
            zone=origin_zone | destination_zone,
            name="Capacity Courier",
            max_concurrent_shipments=2,
            max_concurrent_weight=10.0,
        )
        shipments = self.env["parcel.shipment"]
        for index in range(2):
            shipment = self.create_shipment(
                packages=[
                    {"weight": 1.0, "weight_uom_id": self.kg_uom.id},
                    {"weight": 2.0, "weight_uom_id": self.kg_uom.id},
                ]
            )
            self._assign_at(
                shipment,
                courier,
                self.NOW - timedelta(days=2) if index == 0 else self.NOW,
            )
            shipments |= shipment

        dashboard = self._dashboard()

        destination_item = next(
            item
            for item in dashboard["zone_pressure"]
            if item["id"] == destination_zone.id
        )
        self.assertEqual(
            destination_item,
            {
                "id": destination_zone.id,
                "name": "Dashboard Destination",
                "code": "DST",
                "active_shipments": 2,
                "delayed_shipments": 1,
                "package_count": 4,
                "coverage_warnings": 0,
                "archived": False,
            },
        )
        lane = next(
            item
            for item in dashboard["lanes"]
            if item["origin_zone"]["id"] == origin_zone.id
            and item["destination_zone"]["id"] == destination_zone.id
        )
        self.assertEqual(
            lane,
            {
                "origin_zone": {
                    "id": origin_zone.id,
                    "name": "Dashboard Origin",
                    "code": "ORG",
                },
                "destination_zone": {
                    "id": destination_zone.id,
                    "name": "Dashboard Destination",
                    "code": "DST",
                },
                "shipment_count": 2,
                "delayed_shipments": 1,
                "package_count": 4,
                "coverage_warnings": 0,
            },
        )
        courier_item = next(
            item for item in dashboard["couriers"] if item["id"] == courier.id
        )
        self.assertEqual(
            courier_item,
            {
                "id": courier.id,
                "name": "Capacity Courier",
                "availability": "available",
                "workload_state": "at_capacity",
                "current_shipment_count": 2,
                "current_weight": 6.0,
                "max_concurrent_shipments": 2,
                "max_concurrent_weight": 10.0,
                "weight_uom": self.kg_uom.name,
            },
        )
        self.assertEqual(
            {item["id"] for item in dashboard["shipments"]} & set(shipments.ids),
            set(shipments.ids),
        )

    def test_courier_workload_state_uses_operational_precedence(self):
        assigned_courier = self.create_courier(
            name="Assigned Courier",
            max_concurrent_shipments=2,
        )
        assigned_shipment = self.create_shipment()
        self._assign_at(assigned_shipment, assigned_courier, self.NOW)

        route_courier = self.create_courier(
            name="On Route Courier",
            max_concurrent_shipments=2,
        )
        route_shipment = self.create_shipment()
        self._assign_at(route_shipment, route_courier, self.NOW)
        route_shipment.action_record_pickup(route_shipment.package_ids.ids)
        route_shipment.action_start_transit()

        unavailable_courier = self.create_courier(
            name="Unavailable Full Courier",
            max_concurrent_shipments=1,
        )
        unavailable_shipment = self.create_shipment()
        self._assign_at(unavailable_shipment, unavailable_courier, self.NOW)
        unavailable_courier.write({"availability": "unavailable"})

        workload_by_id = {
            item["id"]: item["workload_state"] for item in self._dashboard()["couriers"]
        }

        self.assertEqual(workload_by_id[self.courier.id], "available")
        self.assertEqual(workload_by_id[assigned_courier.id], "assigned")
        self.assertEqual(workload_by_id[route_courier.id], "on_route")
        self.assertEqual(workload_by_id[unavailable_courier.id], "unavailable")

    def test_operational_queue_is_bounded_and_prioritizes_exceptions(self):
        self.create_shipment(
            recipient=self._create_uncovered_recipient(name="Priority Warning"),
        )
        delayed = self.create_shipment()
        self._assign_at(delayed, self.courier, self.NOW - timedelta(days=2))
        normal = self.env["parcel.shipment"]
        for _index in range(55):
            normal |= self.create_shipment()

        dashboard = self._dashboard()

        zone_item = next(
            item for item in dashboard["zone_pressure"] if item["id"] == self.zone.id
        )
        self.assertEqual(zone_item["active_shipments"], 56)
        self.assertEqual(zone_item["package_count"], 56)
        self.assertEqual(dashboard["lanes"][0]["shipment_count"], 56)
        self.assertEqual(dashboard["lanes"][0]["delayed_shipments"], 1)
        self.assertEqual(dashboard["queue_total"], 57)
        self.assertTrue(dashboard["queue_truncated"])

    def test_activity_is_limited_to_eight_most_recent_events(self):
        courier = self.create_courier(
            name="Activity Courier",
            max_concurrent_shipments=20,
            max_concurrent_weight=1000.0,
        )
        events = []
        for index in range(10):
            shipment = self.create_shipment()
            self._assign_at(shipment, courier, self.NOW - timedelta(hours=1))
            event = shipment.action_record_pickup(shipment.package_ids.ids)
            occurred_at = self.NOW - timedelta(minutes=10 - index)
            self._set_event_time(event, occurred_at)
            events.append((event, shipment, occurred_at))

        activity = self._dashboard()["activity"]

        self.assertEqual(len(activity), 8)
        self.assertEqual(
            [item["shipment_reference"] for item in activity],
            [shipment.reference for _event, shipment, _when in reversed(events[-8:])],
        )
        for item, (_event, shipment, occurred_at) in zip(
            activity, reversed(events[-8:]), strict=True
        ):
            self.assertEqual(
                item,
                {
                    "event_type": "pickup",
                    "occurred_at": fields.Datetime.to_string(occurred_at),
                    "shipment_reference": shipment.reference,
                    "package_count": 1,
                },
            )

    def test_zone_pressure_and_couriers_include_archived_queue_endpoints(self):
        shipment = self.create_shipment()
        loaded_courier = self.create_courier(
            name="A Loaded Dashboard Courier",
            max_concurrent_shipments=5,
            max_concurrent_weight=100.0,
        )
        self._assign_at(shipment, loaded_courier, self.NOW)
        self.zone.write({"active": False})
        self.env.flush_all()
        self.env["parcel.delivery.zone"].create(
            [
                {
                    "name": f"Dashboard Zone {index:03d}",
                    "company_id": self.company.id,
                    "default_sla_hours": 24.0,
                }
                for index in range(100)
            ]
        )
        self.env["parcel.courier"].create(
            [
                {
                    "name": f"Dashboard Courier {index:03d}",
                    "company_id": self.company.id,
                    "max_concurrent_shipments": 8,
                    "max_concurrent_weight": 150.0,
                    "max_weight_uom_id": self.kg_uom.id,
                }
                for index in range(53)
            ]
        )

        dashboard = self._dashboard()

        self.assertEqual(len(dashboard["zone_pressure"]), 1)
        self.assertEqual(dashboard["zone_pressure_total"], 1)
        self.assertFalse(dashboard["zone_pressure_truncated"])
        archived = dashboard["zone_pressure"][0]
        self.assertEqual(archived["id"], self.zone.id)
        self.assertTrue(archived["archived"])
        self.assertEqual(len(dashboard["lanes"]), 1)
        self.assertEqual(dashboard["lane_total"], 1)
        self.assertFalse(dashboard["lanes_truncated"])
        self.assertEqual(dashboard["lanes"][0]["shipment_count"], 1)
        self.assertEqual(len(dashboard["couriers"]), 50)
        self.assertEqual(dashboard["courier_total"], 55)
        self.assertTrue(dashboard["couriers_truncated"])
        loaded = next(
            item for item in dashboard["couriers"] if item["id"] == loaded_courier.id
        )
        self.assertEqual(loaded["current_shipment_count"], 1)
        self.assertEqual(loaded["current_weight"], 1.0)

    def test_delivered_today_uses_user_local_day_utc_boundaries(self):
        local_today = self.create_shipment()
        self._complete_delivery(
            local_today,
            self.courier,
            datetime(2026, 4, 15, 10, 30, 0),
        )
        adjacent_local_day = self.create_shipment()
        self._complete_delivery(
            adjacent_local_day,
            self.courier,
            datetime(2026, 4, 15, 9, 30, 0),
        )

        dashboard = self._dashboard(tz="Pacific/Kiritimati")

        self.assertEqual(dashboard["stats"]["delivered_today"], 1)

    def test_state_labels_use_the_request_language(self):
        shipment = self.create_shipment()
        self._assign_at(shipment, self.courier, self.NOW)
        self.env["res.lang"]._activate_lang("es_ES")
        module = self.env["ir.module.module"].search(
            [("name", "=", "parcel_transport_management")],
            limit=1,
        )
        module._update_translations(filter_lang="es_ES", overwrite=True)

        dashboard = self._dashboard(lang="es_ES")

        item = next(
            item for item in dashboard["shipments"] if item["id"] == shipment.id
        )
        self.assertEqual(item["state_label"], "Asignado")

    def test_permissions_match_courier_operator_and_manager_access(self):
        courier_user = self._create_user(
            "Dashboard Courier User",
            "parcel_transport_management.group_ptm_courier",
        )
        operator_user = self._create_user(
            "Dashboard Operator User",
            "parcel_transport_management.group_ptm_operator",
        )
        manager_user = self._create_user(
            "Dashboard Manager User",
            "parcel_transport_management.group_ptm_manager",
        )
        self.courier.user_id = courier_user

        courier_permissions = self._dashboard(user=courier_user)["permissions"]
        operator_permissions = self._dashboard(user=operator_user)["permissions"]
        manager_permissions = self._dashboard(user=manager_user)["permissions"]

        self.assertEqual(
            courier_permissions,
            {
                "can_create_shipments": False,
                "can_create_couriers": False,
                "can_manage_zones": False,
            },
        )
        self.assertEqual(
            operator_permissions,
            {
                "can_create_shipments": True,
                "can_create_couriers": True,
                "can_manage_zones": False,
            },
        )
        self.assertEqual(
            manager_permissions,
            {
                "can_create_shipments": True,
                "can_create_couriers": True,
                "can_manage_zones": True,
            },
        )

    def test_courier_aggregates_do_not_reveal_other_couriers_load(self):
        courier_user = self._create_user(
            "Dashboard Isolated Courier",
            "parcel_transport_management.group_ptm_courier",
        )
        self.courier.user_id = courier_user
        own_shipment = self.create_shipment()
        self._assign_at(own_shipment, self.courier, self.NOW)
        other_courier = self.create_courier(name="Other Dashboard Courier")
        other_shipment = self.create_shipment(
            packages=[
                {"weight": 2.0, "weight_uom_id": self.kg_uom.id},
                {"weight": 3.0, "weight_uom_id": self.kg_uom.id},
            ]
        )
        self._assign_at(other_shipment, other_courier, self.NOW)

        dashboard = self._dashboard(user=courier_user)

        self.assertEqual(dashboard["stats"]["total_shipments"], 1)
        self.assertEqual(dashboard["stats"]["total_packages"], 1)
        self.assertEqual(dashboard["queue_total"], 1)
        self.assertEqual(
            {item["id"] for item in dashboard["shipments"]}, {own_shipment.id}
        )
        self.assertEqual(
            {item["id"] for item in dashboard["couriers"]}, {self.courier.id}
        )
        zone_item = next(
            item for item in dashboard["zone_pressure"] if item["id"] == self.zone.id
        )
        self.assertEqual(zone_item["active_shipments"], 1)
        self.assertEqual(zone_item["package_count"], 1)
        self.assertEqual(
            dashboard["lanes"],
            [
                {
                    "origin_zone": {
                        "id": self.zone.id,
                        "name": self.zone.name,
                        "code": "",
                    },
                    "destination_zone": {
                        "id": self.zone.id,
                        "name": self.zone.name,
                        "code": "",
                    },
                    "shipment_count": 1,
                    "delayed_shipments": 0,
                    "package_count": 1,
                    "coverage_warnings": 0,
                }
            ],
        )

    def test_operator_with_courier_group_keeps_company_wide_aggregates(self):
        mixed_user = self._create_user(
            "Dashboard Mixed Operator",
            "parcel_transport_management.group_ptm_operator",
        )
        courier_group = self.env.ref("parcel_transport_management.group_ptm_courier")
        mixed_user.group_ids = [Command.link(courier_group.id)]
        self.assertTrue(
            mixed_user.has_group("parcel_transport_management.group_ptm_courier")
        )
        self.assertTrue(
            mixed_user.has_group("parcel_transport_management.group_ptm_operator")
        )
        self.courier.user_id = mixed_user
        own_shipment = self.create_shipment()
        self._assign_at(own_shipment, self.courier, self.NOW)
        other_courier = self.create_courier(name="Mixed User Other Courier")
        other_shipment = self.create_shipment(
            packages=[
                {"weight": 2.0, "weight_uom_id": self.kg_uom.id},
                {"weight": 3.0, "weight_uom_id": self.kg_uom.id},
            ]
        )
        self._assign_at(other_shipment, other_courier, self.NOW)

        dashboard = self._dashboard(user=mixed_user)

        self.assertEqual(dashboard["stats"]["total_shipments"], 2)
        self.assertEqual(dashboard["stats"]["total_packages"], 3)
        self.assertEqual(dashboard["queue_total"], 2)
        self.assertEqual(
            {item["id"] for item in dashboard["couriers"]},
            {self.courier.id, other_courier.id},
        )
        zone_item = next(
            item for item in dashboard["zone_pressure"] if item["id"] == self.zone.id
        )
        self.assertEqual(zone_item["active_shipments"], 2)
        self.assertEqual(zone_item["package_count"], 3)

    def test_dashboard_is_strictly_isolated_by_current_company(self):
        local = self.create_shipment()
        self._assign_at(local, self.courier, self.NOW)
        other_zone = (
            self.env["parcel.delivery.zone"]
            .with_company(self.other_company)
            .create(
                {
                    "name": "Other Company Zone",
                    "code": "OTH",
                    "company_id": self.other_company.id,
                    "default_sla_hours": 36.0,
                }
            )
        )
        self.env["parcel.zone.postcode.rule"].with_company(self.other_company).create(
            {
                "zone_id": other_zone.id,
                "country_id": self.country.id,
                "postcode_prefix": "28",
            }
        )
        other_courier = self.create_courier(
            company=self.other_company,
            zone=other_zone,
            name="Other Company Courier",
        )
        foreign = self.create_shipment(
            company=self.other_company,
            sender=self.other_sender,
            recipient=self.other_recipient,
        )
        self._assign_at(foreign, other_courier, self.NOW)

        local_dashboard = self._dashboard(self.company)
        other_dashboard = self._dashboard(self.other_company)

        self.assertEqual(local_dashboard["stats"]["total_shipments"], 1)
        self.assertEqual(local_dashboard["stats"]["total_packages"], 1)
        self.assertEqual(local_dashboard["queue_total"], 1)
        self.assertEqual(local_dashboard["zone_pressure_total"], 1)
        self.assertFalse(local_dashboard["zone_pressure_truncated"])
        self.assertEqual(local_dashboard["lane_total"], 1)
        self.assertFalse(local_dashboard["lanes_truncated"])
        self.assertEqual(
            {item["id"] for item in local_dashboard["zone_pressure"]},
            {self.zone.id},
        )
        self.assertEqual(
            {
                (
                    lane["origin_zone"]["id"],
                    lane["destination_zone"]["id"],
                )
                for lane in local_dashboard["lanes"]
            },
            {(self.zone.id, self.zone.id)},
        )
        self.assertNotIn(
            other_zone.id,
            {lane["origin_zone"]["id"] for lane in local_dashboard["lanes"]},
        )

        self.assertEqual(other_dashboard["stats"]["total_shipments"], 1)
        self.assertEqual(other_dashboard["stats"]["total_packages"], 1)
        self.assertEqual(other_dashboard["zone_pressure_total"], 1)
        self.assertFalse(other_dashboard["zone_pressure_truncated"])
        self.assertEqual(other_dashboard["lane_total"], 1)
        self.assertFalse(other_dashboard["lanes_truncated"])
        self.assertEqual(
            {item["id"] for item in other_dashboard["zone_pressure"]},
            {other_zone.id},
        )
        self.assertEqual(
            {
                (
                    lane["origin_zone"]["id"],
                    lane["destination_zone"]["id"],
                )
                for lane in other_dashboard["lanes"]
            },
            {(other_zone.id, other_zone.id)},
        )
