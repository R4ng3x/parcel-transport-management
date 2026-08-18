import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { animationFrame, Deferred, expect, test } from "@odoo/hoot";
import {
    defineModels,
    fields,
    mockService,
    models,
    mountWithCleanup,
    onRpc,
    patchWithCleanup,
} from "@web/../tests/web_test_helpers";
import { ParcelCommandCenter } from "../src/js/parcel_dashboard";

class ParcelShipment extends models.Model {
    _name = "parcel.shipment";

    name = fields.Char({ string: "Tracking Number" });
    reference = fields.Char({ string: "Reference" });

    _records = [{ id: 1, name: "PTM-2026-0001-0001", reference: "PTM-2026-0001-0001" }];
}

defineMailModels();
defineModels([ParcelShipment]);

function getSampleDashboardData() {
    return {
        stats: {
            total_shipments: 5,
            reserved_shipments: 2,
            in_transit_shipments: 1,
            delayed_shipments: 1,
            partial_shipments: 0,
            delivered_today: 1,
            total_packages: 8,
            delivered_packages: 3,
            coverage_warnings: 0,
        },
        shipments: [
            {
                id: 1,
                reference: "PTM-2026-0001-0001",
                state: "in_transit",
                state_label: "In transit",
                origin_zone: { id: 1, name: "Madrid" },
                destination_zone: { id: 2, name: "Barcelona" },
                courier: { id: 3, name: "Speedy Courier" },
                expected_delivery_at: "2026-08-20 18:00:00",
                delay_hours: 0,
                coverage_warning: false,
                picked_up_packages: 1,
                delivered_packages: 0,
                package_count: 1,
                total_weight_kg: 5,
            },
        ],
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
            can_create_shipments: true,
            can_create_couriers: true,
            can_manage_zones: true,
        },
        generated_at: "2026-08-18 10:00:00",
    };
}
function mountCommandCenter() {
    return mountWithCleanup(ParcelCommandCenter, {
        props: {
            action: {},
        },
    });
}

test("ParcelCommandCenter loads and displays snapshot data on mount", async () => {
    onRpc("parcel.shipment", "get_dashboard_data", () => {
        expect.step("get_dashboard_data");
        return getSampleDashboardData();
    });

    await mountCommandCenter();
    await animationFrame();

    expect.verifySteps(["get_dashboard_data"]);
    expect(".o_ptm_kpi").toHaveCount(5);
    expect(".o_ptm_kpi--total strong").toHaveText("5");
    expect(".o_ptm_shipment").toHaveCount(1);
    expect(".o_ptm_shipment_topline strong").toHaveText("PTM-2026-0001-0001");
});

test("ParcelCommandCenter rejects stale RPC responses", async () => {
    const deferred = new Deferred();

    onRpc("parcel.shipment", "get_dashboard_data", () => {
        expect.step("get_dashboard_data");
        return deferred;
    });

    const comp = await mountCommandCenter();
    comp.requestSequence += 1;
    deferred.resolve({
        ...getSampleDashboardData(),
        stats: {
            ...getSampleDashboardData().stats,
            total_shipments: 99,
        },
    });
    await comp.requestTail;
    await animationFrame();

    expect.verifySteps(["get_dashboard_data"]);
    expect(comp.state.hasSnapshot).toBe(false);
    expect(comp.state.data.stats.total_shipments).toBe(0);
});

test("ParcelCommandCenter preserves its last valid snapshot after a refresh error", async () => {
    let requestCount = 0;
    onRpc("parcel.shipment", "get_dashboard_data", () => {
        requestCount += 1;
        if (requestCount === 1) {
            return getSampleDashboardData();
        }
        throw new Error("Refresh failed");
    });

    const comp = await mountCommandCenter();
    await animationFrame();
    await comp.loadDashboardData(false);
    await animationFrame();

    expect(requestCount).toBe(2);
    expect(comp.state.error).toBe(true);
    expect(comp.state.hasSnapshot).toBe(true);
    expect(comp.state.data.stats.total_shipments).toBe(5);
    expect(".o_ptm_kpi--total strong").toHaveText("5");
});

test("ParcelCommandCenter dispatches action service calls on navigation clicks", async () => {
    onRpc("parcel.shipment", "get_dashboard_data", () => getSampleDashboardData());

    mockService("action", {
        doAction(action) {
            expect.step("doAction");
            if (typeof action === "string") {
                expect.step(`action_string:${action}`);
            } else {
                expect.step(`action_model:${action.res_model}`);
            }
            return Promise.resolve(true);
        },
    });

    const comp = await mountCommandCenter();
    await animationFrame();

    comp.openShipments();
    comp.openCouriers();
    comp.openZones();
    comp.openShipment(1);

    expect.verifySteps([
        "doAction",
        "action_string:parcel_transport_management.action_ptm_shipment",
        "doAction",
        "action_string:parcel_transport_management.action_ptm_courier",
        "doAction",
        "action_string:parcel_transport_management.action_ptm_zone",
        "doAction",
        "action_model:parcel.shipment",
    ]);
});

test("ParcelCommandCenter clears its refresh timer on unmount", async () => {
    const clearedTimers = [];
    patchWithCleanup(window, {
        clearInterval(timer) {
            clearedTimers.push(timer);
            return super.clearInterval(timer);
        },
    });
    onRpc("parcel.shipment", "get_dashboard_data", () => getSampleDashboardData());

    const comp = await mountCommandCenter();
    await animationFrame();
    const refreshTimer = comp.refreshTimer;
    comp.__owl__.app.destroy();

    expect(comp.isUnmounted).toBe(true);
    expect(clearedTimers.includes(refreshTimer)).toBe(true);
});
