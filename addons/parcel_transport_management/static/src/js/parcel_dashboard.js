import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { formatFloat } from "@web/views/fields/formatters";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const REFRESH_INTERVAL_MS = 60_000;
const NATIVE_ACTIONS = Object.freeze({
    shipments: "parcel_transport_management.action_ptm_shipment",
    couriers: "parcel_transport_management.action_ptm_courier",
    zones: "parcel_transport_management.action_ptm_zone",
});
const DASHBOARD_TERMS = Object.freeze({
    hourLate: _t("h late"),
});

function emptyDashboardData() {
    return {
        stats: {
            total_shipments: 0,
            reserved_shipments: 0,
            in_transit_shipments: 0,
            delayed_shipments: 0,
            partial_shipments: 0,
            delivered_today: 0,
            total_packages: 0,
            delivered_packages: 0,
            coverage_warnings: 0,
        },
        shipments: [],
        lanes: [],
        lane_total: 0,
        lanes_truncated: false,
        zone_pressure: [],
        zone_pressure_total: 0,
        zone_pressure_truncated: false,
        couriers: [],
        activity: [],
        queue_total: 0,
        queue_truncated: false,
        courier_total: 0,
        couriers_truncated: false,
        permissions: {
            can_create_shipments: false,
            can_create_couriers: false,
            can_manage_zones: false,
        },
        generated_at: "",
    };
}

function normalizeDashboardData(data) {
    const empty = emptyDashboardData();
    const shipments = Array.isArray(data.shipments) ? data.shipments : [];
    const lanes = Array.isArray(data.lanes) ? data.lanes : [];
    const zonePressure = Array.isArray(data.zone_pressure) ? data.zone_pressure : [];
    const couriers = Array.isArray(data.couriers) ? data.couriers : [];
    const activity = Array.isArray(data.activity) ? data.activity : [];
    return {
        ...empty,
        ...data,
        stats: { ...empty.stats, ...(data.stats || {}) },
        shipments,
        lanes,
        lane_total: data.lane_total ?? lanes.length,
        lanes_truncated: Boolean(data.lanes_truncated),
        zone_pressure: zonePressure,
        zone_pressure_total: data.zone_pressure_total ?? zonePressure.length,
        zone_pressure_truncated: Boolean(data.zone_pressure_truncated),
        couriers,
        activity,
        queue_total: data.queue_total ?? shipments.length,
        queue_truncated: Boolean(data.queue_truncated),
        courier_total: data.courier_total ?? couriers.length,
        couriers_truncated: Boolean(data.couriers_truncated),
        permissions: { ...empty.permissions, ...(data.permissions || {}) },
    };
}

export class ParcelCommandCenter extends Component {
    static template = "parcel_transport_management.CommandCenter";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");

        this.requestSequence = 0;
        this.appliedRequestSequence = 0;
        this.requestTail = Promise.resolve();
        this.isUnmounted = false;

        this.state = useState({
            loading: true,
            refreshing: false,
            error: false,
            hasSnapshot: false,
            data: emptyDashboardData(),
        });

        onMounted(() => {
            this.loadDashboardData(true);
            this.refreshTimer = window.setInterval(
                () => this.loadDashboardData(false),
                REFRESH_INTERVAL_MS,
            );
        });
        onWillUnmount(() => {
            this.isUnmounted = true;
            this.requestSequence += 1;
            window.clearInterval(this.refreshTimer);
        });
    }

    async loadDashboardData(initial = false) {
        if (this.isUnmounted) {
            return;
        }
        if (
            this.state.refreshing ||
            (this.state.loading && this.requestSequence > this.appliedRequestSequence)
        ) {
            return this.requestTail;
        }

        const requestId = ++this.requestSequence;
        if (initial && !this.state.hasSnapshot) {
            this.state.loading = true;
        } else {
            this.state.refreshing = true;
        }

        const request = this.requestTail.then(() => {
            if (this.isUnmounted) {
                return null;
            }
            return this.orm.call("parcel.shipment", "get_dashboard_data", []);
        });
        this.requestTail = request.catch(() => undefined);

        try {
            const data = await request;
            if (
                this.isUnmounted ||
                requestId !== this.requestSequence ||
                requestId <= this.appliedRequestSequence
            ) {
                return;
            }
            if (!data) {
                throw new Error("The dashboard response was empty.");
            }
            this.appliedRequestSequence = requestId;
            const normalizedData = normalizeDashboardData(data);
            this.state.data = normalizedData;
            this.state.error = false;
            this.state.hasSnapshot = true;
        } catch {
            if (this.isUnmounted || requestId !== this.requestSequence) {
                return;
            }
            this.state.error = true;
        } finally {
            if (!this.isUnmounted && requestId === this.requestSequence) {
                this.state.loading = false;
                this.state.refreshing = false;
            }
        }
    }

    openShipments() {
        return this.action.doAction(NATIVE_ACTIONS.shipments);
    }

    openCouriers() {
        return this.action.doAction(NATIVE_ACTIONS.couriers);
    }

    openZones() {
        return this.action.doAction(NATIVE_ACTIONS.zones);
    }

    openShipment(shipmentId) {
        return this.openRecord("parcel.shipment", shipmentId);
    }

    openCourier(courierId) {
        return this.openRecord("parcel.courier", courierId);
    }

    openZone(zoneId) {
        return this.openRecord("parcel.delivery.zone", zoneId);
    }

    openRecord(model, recordId) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: recordId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openShipmentReference(reference) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Shipment activity"),
            res_model: "parcel.shipment",
            domain: [["reference", "=", reference]],
            views: [
                [false, "list"],
                [false, "form"],
            ],
            target: "current",
        });
    }

    formatInteger(value) {
        return formatFloat(Number(value) || 0, { digits: [16, 0] });
    }

    formatDecimal(value) {
        return formatFloat(Number(value) || 0, {
            digits: [16, 1],
            trailingZeros: false,
        });
    }

    formatWeight(value, unit = "") {
        const amount = this.formatDecimal(value);
        return unit ? `${amount} ${unit}` : amount;
    }

    formatDateTimeValue(value, format = undefined) {
        if (!value) {
            return "—";
        }
        try {
            const dateTime = deserializeDateTime(value);
            if (!dateTime.isValid) {
                return "—";
            }
            return formatDateTime(dateTime, format ? { format } : undefined) || "—";
        } catch {
            return "—";
        }
    }

    formatExpectedAt(value) {
        return this.formatDateTimeValue(value, "dd LLL · HH:mm");
    }

    formatActivityTime(value) {
        return this.formatDateTimeValue(value, "HH:mm");
    }

    formatGeneratedAt(value) {
        return this.formatDateTimeValue(value, "dd LLL yyyy · HH:mm");
    }

    formatDelay(value) {
        return `${this.formatDecimal(value)} ${DASHBOARD_TERMS.hourLate}`;
    }

    zoneName(zone) {
        return zone ? zone.name : _t("Unmapped");
    }

    courierName(courier) {
        return courier ? courier.name : _t("Unassigned");
    }

    shipmentClass(shipment) {
        const classes = [
            {
                draft: "o_ptm_shipment--draft",
                assigned: "o_ptm_shipment--assigned",
                partially_picked_up: "o_ptm_shipment--partial",
                picked_up: "o_ptm_shipment--picked",
                in_transit: "o_ptm_shipment--transit",
                partially_delivered: "o_ptm_shipment--partial",
                delivery_failed: "o_ptm_shipment--failed",
                delivered: "o_ptm_shipment--delivered",
                cancelled: "o_ptm_shipment--cancelled",
            }[shipment.state] || "o_ptm_shipment--draft",
        ];
        if (Number(shipment.delay_hours) > 0) {
            classes.push("o_ptm_shipment--late");
        }
        if (shipment.coverage_warning) {
            classes.push("o_ptm_shipment--coverage");
        }
        return classes.join(" ");
    }

    workloadClass(workloadState) {
        return (
            {
                unavailable: "o_ptm_courier--unavailable",
                at_capacity: "o_ptm_courier--capacity",
                on_route: "o_ptm_courier--route",
                assigned: "o_ptm_courier--assigned",
                available: "o_ptm_courier--available",
            }[workloadState] || "o_ptm_courier--unavailable"
        );
    }

    workloadLabel(workloadState) {
        return (
            {
                unavailable: _t("Unavailable"),
                at_capacity: _t("At capacity"),
                on_route: _t("On route"),
                assigned: _t("Assigned"),
                available: _t("Available"),
            }[workloadState] || _t("Unavailable")
        );
    }

    eventLabel(eventType) {
        return (
            {
                pickup: _t("Pickup recorded"),
                delivery: _t("Delivery recorded"),
            }[eventType] || _t("Shipment event")
        );
    }

    eventClass(eventType) {
        return eventType === "delivery"
            ? "o_ptm_activity_marker--delivery"
            : "o_ptm_activity_marker--pickup";
    }

    courierInitials(name) {
        const initials = String(name || "")
            .trim()
            .split(/\s+/)
            .slice(0, 2)
            .map((part) => part.charAt(0))
            .join("")
            .toUpperCase();
        return initials || "—";
    }

    progressStyle(current, maximum) {
        const max = Number(maximum) || 0;
        const ratio = max > 0 ? ((Number(current) || 0) / max) * 100 : 0;
        const progress = Math.max(0, Math.min(100, ratio));
        return `--o-ptm-progress: ${progress}%`;
    }

    laneKey(lane, index) {
        return `${lane.origin_zone?.id || "origin"}-${lane.destination_zone?.id || "destination"}-${index}`;
    }

    laneClass(lane) {
        const classes = [];
        if (Number(lane.delayed_shipments) > 0) {
            classes.push("o_ptm_lane--alert");
        }
        if (Number(lane.coverage_warnings) > 0) {
            classes.push("o_ptm_lane--coverage");
        }
        return classes.join(" ");
    }

    zonePressureClass(zone) {
        const classes = [];
        if (Number(zone.delayed_shipments) > 0) {
            classes.push("o_ptm_pressure_item--alert");
        }
        if (Number(zone.coverage_warnings) > 0) {
            classes.push("o_ptm_pressure_item--coverage");
        }
        if (zone.archived) {
            classes.push("o_ptm_pressure_item--archived");
        }
        return classes.join(" ");
    }

    zoneCode(zone) {
        return zone?.code || "—";
    }

    activityKey(item, index) {
        return `${item.event_type}-${item.occurred_at}-${item.shipment_reference}-${index}`;
    }
}

registry.category("actions").add("parcel_transport_management.CommandCenter", ParcelCommandCenter);
