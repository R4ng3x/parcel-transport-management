from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz
from odoo import api, fields, models
from odoo.tools import SQL

from .shipment import RESERVED_STATES

OPEN_STATES = ("draft", "delivery_failed", *RESERVED_STATES)
ON_ROUTE_STATES = (
    "partially_picked_up",
    "picked_up",
    "in_transit",
    "partially_delivered",
)
PARTIAL_STATES = ("partially_picked_up", "partially_delivered")

QUEUE_LIMIT = 50
LANE_LIMIT = 8
ZONE_PRESSURE_LIMIT = 8
COURIER_LIMIT = 50
ACTIVITY_LIMIT = 8


class ParcelShipmentDashboard(models.Model):
    _inherit = "parcel.shipment"

    @api.model
    def get_dashboard_data(self):
        company_id = self.env.company.id
        now = fields.Datetime.to_datetime(fields.Datetime.now())
        shipment_domain = [("company_id", "=", company_id)]
        open_domain = [
            *shipment_domain,
            ("state", "in", OPEN_STATES),
        ]
        is_courier = self.env.user.has_group(
            "parcel_transport_management.group_ptm_courier"
        )
        is_operator = self.env.user.has_group(
            "parcel_transport_management.group_ptm_operator"
        )
        is_manager = self.env.user.has_group(
            "parcel_transport_management.group_ptm_manager"
        )
        shipment_visibility_sql = SQL("TRUE")
        if is_courier and not is_operator:
            shipment_visibility_sql = SQL(
                """
                EXISTS (
                    SELECT 1
                    FROM %s AS visible_courier
                    WHERE visible_courier.id = shipment.courier_id
                        AND visible_courier.company_id = %s
                        AND visible_courier.user_id = %s
                )
                """,
                SQL.identifier(self.env["parcel.courier"]._table),
                company_id,
                self.env.user.id,
            )

        timezone_name = self.env.context.get("tz") or self.env.user.tz or "UTC"
        timezone = pytz.timezone(timezone_name)
        utc_now = (
            pytz.UTC.localize(now) if now.tzinfo is None else now.astimezone(pytz.UTC)
        )
        local_date = utc_now.astimezone(timezone).date()
        today_start = timezone.localize(datetime.combine(local_date, time.min))
        tomorrow_start = timezone.localize(
            datetime.combine(local_date + timedelta(days=1), time.min)
        )
        today_start_utc = today_start.astimezone(pytz.UTC).replace(tzinfo=None)
        tomorrow_start_utc = tomorrow_start.astimezone(pytz.UTC).replace(tzinfo=None)
        if is_operator:
            self = self.sudo()

        stats = {
            "total_shipments": self.search_count(shipment_domain),
            "reserved_shipments": self.search_count(
                [*shipment_domain, ("state", "in", RESERVED_STATES)]
            ),
            "in_transit_shipments": self.search_count(
                [*shipment_domain, ("state", "=", "in_transit")]
            ),
            "delayed_shipments": self.search_count(
                [*open_domain, ("expected_delivery_at", "<", now)]
            ),
            "partial_shipments": self.search_count(
                [*shipment_domain, ("state", "in", PARTIAL_STATES)]
            ),
            "delivered_today": self.search_count(
                [
                    *shipment_domain,
                    ("state", "=", "delivered"),
                    ("delivered_at", ">=", today_start_utc),
                    ("delivered_at", "<", tomorrow_start_utc),
                ]
            ),
            "total_packages": self.env["parcel.package"].search_count(
                [("company_id", "=", company_id)]
            ),
            "delivered_packages": self.env["parcel.package"].search_count(
                [
                    ("company_id", "=", company_id),
                    ("delivery_event_id", "!=", False),
                ]
            ),
            "coverage_warnings": self.search_count(
                [*open_domain, ("coverage_warning", "!=", False)]
            ),
        }

        queue_total = self.search_count(open_domain)
        queue_ids = []
        queue_partitions = (
            (
                [
                    *open_domain,
                    ("coverage_warning", "!=", False),
                    ("expected_delivery_at", "<", now),
                ],
                "expected_delivery_at, id desc",
            ),
            (
                [
                    *open_domain,
                    ("coverage_warning", "!=", False),
                    "|",
                    ("expected_delivery_at", "=", False),
                    ("expected_delivery_at", ">=", now),
                ],
                "id desc",
            ),
            (
                [
                    *open_domain,
                    ("coverage_warning", "=", False),
                    ("expected_delivery_at", "<", now),
                ],
                "expected_delivery_at, id desc",
            ),
            (
                [
                    *open_domain,
                    ("coverage_warning", "=", False),
                    "|",
                    ("expected_delivery_at", "=", False),
                    ("expected_delivery_at", ">=", now),
                ],
                "id desc",
            ),
        )
        for domain, order in queue_partitions:
            remaining = QUEUE_LIMIT - len(queue_ids)
            if not remaining:
                break
            queue_ids.extend(self.search(domain, order=order, limit=remaining).ids)
        queue = self.browse(queue_ids)

        package_data = defaultdict(
            lambda: {
                "count": 0,
                "picked_up": 0,
                "delivered": 0,
                "weight_kg": 0.0,
            }
        )
        if queue_ids:
            for (
                shipment,
                package_count,
                picked_up_count,
                delivered_count,
                weight_kg,
            ) in self.env["parcel.package"]._read_group(
                [
                    ("company_id", "=", company_id),
                    ("shipment_id", "in", queue_ids),
                ],
                ["shipment_id"],
                [
                    "__count",
                    "pickup_event_id:count",
                    "delivery_event_id:count",
                    "weight_kg:sum",
                ],
            ):
                package_data[shipment.id] = {
                    "count": package_count,
                    "picked_up": picked_up_count,
                    "delivered": delivered_count,
                    "weight_kg": weight_kg or 0.0,
                }

        delay_data = {}
        for shipment in queue:
            delay_hours = (
                max(
                    0.0,
                    (now - shipment.expected_delivery_at).total_seconds() / 3600.0,
                )
                if shipment.expected_delivery_at
                else 0.0
            )
            original_delay_hours = (
                max(
                    0.0,
                    (now - shipment.original_expected_delivery_at).total_seconds()
                    / 3600.0,
                )
                if shipment.original_expected_delivery_at
                else 0.0
            )
            delay_data[shipment.id] = (delay_hours, original_delay_hours)

        lane_rows = []
        self.env.cr.execute(
            SQL(
                """
                SELECT
                    shipment.origin_zone_id,
                    origin_zone.name,
                    origin_zone.code,
                    shipment.destination_zone_id,
                    destination_zone.name,
                    destination_zone.code,
                    COUNT(DISTINCT shipment.id) AS shipment_count,
                    COUNT(DISTINCT shipment.id) FILTER (
                        WHERE shipment.expected_delivery_at < %s
                    ) AS delayed_shipments,
                    COUNT(package.id) AS package_count,
                    COUNT(DISTINCT shipment.id) FILTER (
                        WHERE shipment.coverage_warning IS NOT NULL
                            AND shipment.coverage_warning <> ''
                    ) AS coverage_warnings,
                    COUNT(*) OVER () AS lane_total
                FROM %s AS shipment
                JOIN %s AS origin_zone
                    ON origin_zone.id = shipment.origin_zone_id
                    AND origin_zone.company_id = shipment.company_id
                JOIN %s AS destination_zone
                    ON destination_zone.id = shipment.destination_zone_id
                    AND destination_zone.company_id = shipment.company_id
                LEFT JOIN %s AS package
                    ON package.shipment_id = shipment.id
                WHERE shipment.company_id = %s
                    AND shipment.state IN %s
                    AND %s
                GROUP BY
                    shipment.origin_zone_id,
                    origin_zone.name,
                    origin_zone.code,
                    shipment.destination_zone_id,
                    destination_zone.name,
                    destination_zone.code
                ORDER BY
                    delayed_shipments DESC,
                    coverage_warnings DESC,
                    shipment_count DESC,
                    package_count DESC,
                    origin_zone.name,
                    destination_zone.name,
                    shipment.origin_zone_id,
                    shipment.destination_zone_id
                LIMIT %s
                """,
                now,
                SQL.identifier(self._table),
                SQL.identifier(self.env["parcel.delivery.zone"]._table),
                SQL.identifier(self.env["parcel.delivery.zone"]._table),
                SQL.identifier(self.env["parcel.package"]._table),
                company_id,
                tuple(OPEN_STATES),
                shipment_visibility_sql,
                LANE_LIMIT,
            )
        )
        lane_rows = self.env.cr.fetchall()
        lane_total = int(lane_rows[0][-1]) if lane_rows else 0

        zone_pressure_rows = []
        self.env.cr.execute(
            SQL(
                """
                SELECT
                    destination_zone.id,
                    destination_zone.name,
                    destination_zone.code,
                    COUNT(DISTINCT shipment.id) AS shipment_count,
                    COUNT(DISTINCT shipment.id) FILTER (
                        WHERE shipment.expected_delivery_at < %s
                    ) AS delayed_shipments,
                    COUNT(package.id) AS package_count,
                    COUNT(DISTINCT shipment.id) FILTER (
                        WHERE shipment.coverage_warning IS NOT NULL
                            AND shipment.coverage_warning <> ''
                    ) AS coverage_warnings,
                    NOT destination_zone.active AS archived,
                    COUNT(*) OVER () AS zone_pressure_total
                FROM %s AS shipment
                JOIN %s AS destination_zone
                    ON destination_zone.id = shipment.destination_zone_id
                    AND destination_zone.company_id = shipment.company_id
                LEFT JOIN %s AS package
                    ON package.shipment_id = shipment.id
                WHERE shipment.company_id = %s
                    AND shipment.state IN %s
                    AND %s
                GROUP BY
                    destination_zone.id,
                    destination_zone.name,
                    destination_zone.code,
                    destination_zone.active
                ORDER BY
                    delayed_shipments DESC,
                    coverage_warnings DESC,
                    shipment_count DESC,
                    package_count DESC,
                    destination_zone.name,
                    destination_zone.id
                LIMIT %s
                """,
                now,
                SQL.identifier(self._table),
                SQL.identifier(self.env["parcel.delivery.zone"]._table),
                SQL.identifier(self.env["parcel.package"]._table),
                company_id,
                tuple(OPEN_STATES),
                shipment_visibility_sql,
                ZONE_PRESSURE_LIMIT,
            )
        )
        zone_pressure_rows = self.env.cr.fetchall()
        zone_pressure_total = (
            int(zone_pressure_rows[0][-1]) if zone_pressure_rows else 0
        )
        courier_model = self.env["parcel.courier"]
        courier_domain = [("company_id", "=", company_id)]
        courier_total = courier_model.search_count(courier_domain)
        couriers = courier_model.search(
            courier_domain,
            order="name, id",
            limit=COURIER_LIMIT,
        )
        courier_ids = couriers.ids
        courier_counts = defaultdict(int)
        courier_states = defaultdict(set)
        courier_weight_kg = defaultdict(float)
        if courier_ids:
            self.env.cr.execute(
                SQL(
                    """
                    SELECT
                        shipment.courier_id,
                        shipment.state,
                        COUNT(DISTINCT shipment.id),
                        COALESCE(SUM(package.weight_kg), 0.0)
                    FROM %s AS shipment
                    LEFT JOIN %s AS package
                        ON package.shipment_id = shipment.id
                    WHERE shipment.company_id = %s
                        AND shipment.state IN %s
                        AND shipment.courier_id IN %s
                        AND %s
                    GROUP BY shipment.courier_id, shipment.state
                    ORDER BY shipment.courier_id, shipment.state
                    """,
                    SQL.identifier(self._table),
                    SQL.identifier(self.env["parcel.package"]._table),
                    company_id,
                    tuple(RESERVED_STATES),
                    tuple(courier_ids),
                    shipment_visibility_sql,
                )
            )
            for courier_id, state, shipment_count, weight_kg in self.env.cr.fetchall():
                courier_counts[courier_id] += shipment_count
                courier_states[courier_id].add(state)
                courier_weight_kg[courier_id] += weight_kg

        kilogram = self.env.ref("uom.product_uom_kgm")
        courier_items = []
        for courier in couriers:
            current_shipment_count = courier_counts[courier.id]
            current_weight = float(
                kilogram._compute_quantity(
                    courier_weight_kg[courier.id],
                    courier.max_weight_uom_id,
                    round=False,
                )
            )
            if courier.availability == "unavailable":
                workload_state = "unavailable"
            elif (
                current_shipment_count >= courier.max_concurrent_shipments
                or current_weight >= courier.max_concurrent_weight
            ):
                workload_state = "at_capacity"
            elif courier_states[courier.id].intersection(ON_ROUTE_STATES):
                workload_state = "on_route"
            elif current_shipment_count:
                workload_state = "assigned"
            else:
                workload_state = "available"
            courier_items.append(
                {
                    "id": courier.id,
                    "name": courier.name,
                    "availability": courier.availability,
                    "workload_state": workload_state,
                    "current_shipment_count": current_shipment_count,
                    "current_weight": current_weight,
                    "max_concurrent_shipments": courier.max_concurrent_shipments,
                    "max_concurrent_weight": float(courier.max_concurrent_weight),
                    "weight_uom": courier.max_weight_uom_id.name,
                }
            )

        state_labels = dict(self._fields["state"]._description_selection(self.env))

        def shipment_item(shipment):
            summary = package_data[shipment.id]
            delay_hours, original_delay_hours = delay_data[shipment.id]
            return {
                "id": shipment.id,
                "reference": shipment.reference,
                "state": shipment.state,
                "state_label": state_labels[shipment.state],
                "expected_delivery_at": fields.Datetime.to_string(
                    shipment.expected_delivery_at
                )
                if shipment.expected_delivery_at
                else False,
                "delay_hours": float(delay_hours),
                "original_delay_hours": float(original_delay_hours),
                "coverage_warning": bool(shipment.coverage_warning),
                "coverage_warning_reason": shipment.coverage_warning or False,
                "package_count": summary["count"],
                "picked_up_count": summary["picked_up"],
                "delivered_count": summary["delivered"],
                "total_weight_kg": float(summary["weight_kg"]),
                "courier": {
                    "id": shipment.courier_id.id,
                    "name": shipment.courier_id.name,
                }
                if shipment.courier_id
                else False,
                "origin_zone": {
                    "id": shipment.origin_zone_id.id,
                    "name": shipment.origin_zone_id.name,
                }
                if shipment.origin_zone_id
                else False,
                "destination_zone": {
                    "id": shipment.destination_zone_id.id,
                    "name": shipment.destination_zone_id.name,
                }
                if shipment.destination_zone_id
                else False,
            }

        pickup_activity = self.env["parcel.pickup.event"].search(
            [("company_id", "=", company_id)],
            order="occurred_at desc, id desc",
            limit=ACTIVITY_LIMIT,
        )
        delivery_activity = self.env["parcel.delivery.event"].search(
            [("company_id", "=", company_id)],
            order="occurred_at desc, id desc",
            limit=ACTIVITY_LIMIT,
        )
        activity_events = [
            (event.occurred_at, event.id, "pickup", event) for event in pickup_activity
        ] + [
            (event.occurred_at, event.id, "delivery", event)
            for event in delivery_activity
        ]
        activity_events.sort(key=lambda item: (item[0], item[1]), reverse=True)
        activity = [
            {
                "event_type": event_type,
                "occurred_at": fields.Datetime.to_string(event.occurred_at),
                "shipment_reference": event.shipment_id.reference,
                "package_count": len(event.package_ids),
            }
            for _occurred_at, _event_id, event_type, event in activity_events[
                :ACTIVITY_LIMIT
            ]
        ]

        return {
            "stats": stats,
            "shipments": [shipment_item(shipment) for shipment in queue],
            "queue_total": queue_total,
            "queue_truncated": queue_total > len(queue),
            "lanes": [
                {
                    "origin_zone": {
                        "id": origin_zone_id,
                        "name": origin_name,
                        "code": origin_code or "",
                    },
                    "destination_zone": {
                        "id": destination_zone_id,
                        "name": destination_name,
                        "code": destination_code or "",
                    },
                    "shipment_count": shipment_count,
                    "delayed_shipments": delayed_count,
                    "package_count": package_count,
                    "coverage_warnings": coverage_warning_count,
                }
                for (
                    origin_zone_id,
                    origin_name,
                    origin_code,
                    destination_zone_id,
                    destination_name,
                    destination_code,
                    shipment_count,
                    delayed_count,
                    package_count,
                    coverage_warning_count,
                    _lane_total,
                ) in lane_rows
            ],
            "lane_total": lane_total,
            "lanes_truncated": lane_total > len(lane_rows),
            "zone_pressure": [
                {
                    "id": zone_id,
                    "name": zone_name,
                    "code": zone_code or "",
                    "active_shipments": shipment_count,
                    "delayed_shipments": delayed_count,
                    "package_count": package_count,
                    "coverage_warnings": coverage_warning_count,
                    "archived": archived,
                }
                for (
                    zone_id,
                    zone_name,
                    zone_code,
                    shipment_count,
                    delayed_count,
                    package_count,
                    coverage_warning_count,
                    archived,
                    _zone_pressure_total,
                ) in zone_pressure_rows
            ],
            "zone_pressure_total": zone_pressure_total,
            "zone_pressure_truncated": zone_pressure_total > len(zone_pressure_rows),
            "couriers": courier_items,
            "courier_total": courier_total,
            "couriers_truncated": courier_total > len(courier_items),
            "activity": activity,
            "permissions": {
                "can_create_shipments": is_operator or is_manager,
                "can_create_couriers": is_operator or is_manager,
                "can_manage_zones": is_manager,
            },
            "generated_at": fields.Datetime.to_string(now),
        }
