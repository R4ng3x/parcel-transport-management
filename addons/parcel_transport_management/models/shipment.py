from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL

from .reassignment import REASSIGNMENT_CREATE_CONTEXT, REASSIGNMENT_CREATE_TOKEN

RESERVED_STATES = (
    "assigned",
    "partially_picked_up",
    "picked_up",
    "in_transit",
    "partially_delivered",
)

ROUTE_SNAPSHOT_FIELDS = (
    "pickup_name",
    "pickup_street",
    "pickup_street2",
    "pickup_city",
    "pickup_zip",
    "pickup_country_id",
    "delivery_name",
    "delivery_street",
    "delivery_street2",
    "delivery_city",
    "delivery_zip",
    "delivery_country_id",
)
ROUTE_COUNTRY_FIELDS = {"pickup_country_id", "delivery_country_id"}


class ParcelShipment(models.Model):
    _name = "parcel.shipment"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Parcel Shipment"
    _order = "id desc"
    _check_company_auto = True

    reference = fields.Char(
        required=True,
        readonly=True,
        copy=False,
        default="New",
        index=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    sender_id = fields.Many2one(
        "res.partner",
        required=True,
        ondelete="restrict",
        check_company=True,
        index=True,
        tracking=True,
    )
    recipient_id = fields.Many2one(
        "res.partner",
        required=True,
        ondelete="restrict",
        check_company=True,
        index=True,
        tracking=True,
    )
    courier_id = fields.Many2one(
        "parcel.courier",
        readonly=True,
        ondelete="restrict",
        check_company=True,
        index=True,
        copy=False,
        tracking=True,
    )
    package_ids = fields.One2many(
        "parcel.package",
        "shipment_id",
        string="Packages",
        copy=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("assigned", "Assigned"),
            ("partially_picked_up", "Partially Picked Up"),
            ("picked_up", "Picked Up"),
            ("in_transit", "In Transit"),
            ("partially_delivered", "Partially Delivered"),
            ("delivered", "Delivered"),
            ("cancelled", "Cancelled"),
        ],
        required=True,
        readonly=True,
        default="draft",
        copy=False,
        index=True,
        tracking=True,
    )

    pickup_name = fields.Char(readonly=True)
    pickup_street = fields.Char(readonly=True)
    pickup_street2 = fields.Char(readonly=True)
    pickup_city = fields.Char(readonly=True)
    pickup_zip = fields.Char(readonly=True)
    pickup_country_id = fields.Many2one(
        "res.country", readonly=True, ondelete="restrict"
    )
    delivery_name = fields.Char(readonly=True)
    delivery_street = fields.Char(readonly=True)
    delivery_street2 = fields.Char(readonly=True)
    delivery_city = fields.Char(readonly=True)
    delivery_zip = fields.Char(readonly=True)
    delivery_country_id = fields.Many2one(
        "res.country", readonly=True, ondelete="restrict"
    )
    origin_zone_id = fields.Many2one(
        "parcel.delivery.zone",
        readonly=True,
        ondelete="restrict",
        check_company=True,
        index=True,
        tracking=True,
    )
    destination_zone_id = fields.Many2one(
        "parcel.delivery.zone",
        readonly=True,
        ondelete="restrict",
        check_company=True,
        index=True,
        tracking=True,
    )
    coverage_warning = fields.Text(readonly=True, copy=False, tracking=True)

    expected_delivery_at = fields.Datetime(copy=False, tracking=True)
    original_expected_delivery_at = fields.Datetime(readonly=True, copy=False)
    first_picked_up_at = fields.Datetime(readonly=True, copy=False)
    transit_started_at = fields.Datetime(readonly=True, copy=False)
    delivered_at = fields.Datetime(readonly=True, copy=False)
    sla_revision_ids = fields.One2many(
        "parcel.sla.revision",
        "shipment_id",
        string="SLA Revisions",
        readonly=True,
    )
    route_correction_ids = fields.One2many(
        "parcel.route.correction",
        "shipment_id",
        string="Route Corrections",
        readonly=True,
    )
    courier_reassignment_ids = fields.One2many(
        "parcel.courier.reassignment",
        "shipment_id",
        string="Courier Reassignments",
        readonly=True,
    )
    delay_hours = fields.Float(
        compute="_compute_delay_hours",
        store=True,
        readonly=True,
        copy=False,
    )
    original_delay_hours = fields.Float(
        compute="_compute_delay_hours",
        store=True,
        readonly=True,
        copy=False,
    )
    cancellation_reason = fields.Text(readonly=True, copy=False, tracking=True)
    cancelled_at = fields.Datetime(readonly=True, copy=False)
    total_weight_kg = fields.Float(
        compute="_compute_total_weight_kg",
        string="Total Weight (kg)",
        digits=(16, 6),
    )

    _reference_unique = models.Constraint(
        "UNIQUE(reference)", "Shipment references must be unique."
    )

    @api.depends("package_ids.weight_kg")
    def _compute_total_weight_kg(self):
        for shipment in self:
            shipment.total_weight_kg = sum(shipment.package_ids.mapped("weight_kg"))

    @api.depends(
        "delivered_at",
        "expected_delivery_at",
        "original_expected_delivery_at",
    )
    def _compute_delay_hours(self):
        for shipment in self:
            shipment.delay_hours = 0.0
            shipment.original_delay_hours = 0.0
            if not shipment.delivered_at:
                continue
            if shipment.expected_delivery_at:
                shipment.delay_hours = max(
                    0.0,
                    (
                        shipment.delivered_at - shipment.expected_delivery_at
                    ).total_seconds()
                    / 3600.0,
                )
            if shipment.original_expected_delivery_at:
                shipment.original_delay_hours = max(
                    0.0,
                    (
                        shipment.delivered_at - shipment.original_expected_delivery_at
                    ).total_seconds()
                    / 3600.0,
                )

    @api.model
    def _new_reference(self):
        return self.env["ir.sequence"].next_by_code("parcel.shipment") or "New"

    @api.model
    def _snapshot_values(self, company, sender, recipient):
        rule_model = self.env["parcel.zone.postcode.rule"]
        origin_zone = rule_model._resolve(company, sender.country_id, sender.zip)
        destination_zone = rule_model._resolve(
            company, recipient.country_id, recipient.zip
        )
        warnings = []
        if not origin_zone:
            warnings.append(
                _("The pickup address is outside configured zone coverage.")
            )
        if not destination_zone:
            warnings.append(
                _("The delivery address is outside configured zone coverage.")
            )
        return {
            "pickup_name": sender.name,
            "pickup_street": sender.street,
            "pickup_street2": sender.street2,
            "pickup_city": sender.city,
            "pickup_zip": sender.zip,
            "pickup_country_id": sender.country_id.id or False,
            "delivery_name": recipient.name,
            "delivery_street": recipient.street,
            "delivery_street2": recipient.street2,
            "delivery_city": recipient.city,
            "delivery_zip": recipient.zip,
            "delivery_country_id": recipient.country_id.id or False,
            "origin_zone_id": origin_zone.id or False,
            "destination_zone_id": destination_zone.id or False,
            "coverage_warning": "\n".join(warnings) or False,
        }

    @api.model_create_multi
    def create(self, vals_list):
        protected = {
            "state",
            "courier_id",
            "original_expected_delivery_at",
            "cancellation_reason",
            "cancelled_at",
            "first_picked_up_at",
            "transit_started_at",
            "delivered_at",
            "delay_hours",
            "original_delay_hours",
        }
        snapshot_fields = {
            "pickup_name",
            "pickup_street",
            "pickup_street2",
            "pickup_city",
            "pickup_zip",
            "pickup_country_id",
            "delivery_name",
            "delivery_street",
            "delivery_street2",
            "delivery_city",
            "delivery_zip",
            "delivery_country_id",
            "origin_zone_id",
            "destination_zone_id",
            "coverage_warning",
        }
        prepared = []
        for incoming in vals_list:
            values = dict(incoming)
            company_id = values.get("company_id") or self.env.company.id
            if not self.env.su and company_id not in self.env.companies.ids:
                raise AccessError(
                    _("Shipments can only be created in an allowed company.")
                )
            if not self.env.su and protected.intersection(values):
                raise AccessError(
                    _("Operational shipment fields cannot be set directly.")
                )
            if "reference" in values and values["reference"] not in (False, "New"):
                raise ValidationError(
                    _("Shipment references are generated by the server.")
                )
            values["reference"] = self._new_reference()
            company = (
                self.env["res.company"]
                .browse(values.get("company_id") or self.env.company.id)
                .exists()
            )
            sender = self.env["res.partner"].browse(values.get("sender_id")).exists()
            recipient = (
                self.env["res.partner"].browse(values.get("recipient_id")).exists()
            )
            if not company or not sender or not recipient:
                raise ValidationError(
                    _("A company, sender, and recipient are required.")
                )
            for field_name in snapshot_fields:
                values.pop(field_name, None)
            values.update(self._snapshot_values(company, sender, recipient))
            prepared.append(values)
        return super().create(prepared)

    def write(self, vals):
        protected = {
            "state",
            "courier_id",
            "original_expected_delivery_at",
            "cancellation_reason",
            "cancelled_at",
            "first_picked_up_at",
            "transit_started_at",
            "delivered_at",
            "delay_hours",
            "original_delay_hours",
            "pickup_name",
            "pickup_street",
            "pickup_street2",
            "pickup_city",
            "pickup_zip",
            "pickup_country_id",
            "delivery_name",
            "delivery_street",
            "delivery_street2",
            "delivery_city",
            "delivery_zip",
            "delivery_country_id",
            "origin_zone_id",
            "destination_zone_id",
            "coverage_warning",
            "reference",
        }
        if protected.intersection(vals) and not self.env.su:
            raise AccessError(
                _("Operational shipment fields cannot be written directly.")
            )

        if "company_id" in vals and any(
            shipment.company_id.id != vals["company_id"] for shipment in self
        ):
            raise AccessError(_("A shipment's company cannot be changed."))

        mutable = {
            "sender_id",
            "recipient_id",
            "package_ids",
            "expected_delivery_at",
        }
        if mutable.intersection(vals):
            self._lock_shipments()
            invalid = self.filtered(lambda shipment: shipment.state != "draft")
            if invalid:
                raise UserError(_("Shipment details can only be changed in draft."))

        if {"sender_id", "recipient_id"}.intersection(vals):
            if len(self) != 1:
                raise UserError(
                    _("Address parties must be changed one shipment at a time.")
                )
            shipment = self
            company = shipment.company_id
            sender = (
                self.env["res.partner"]
                .browse(vals.get("sender_id", shipment.sender_id.id))
                .exists()
            )
            recipient = (
                self.env["res.partner"]
                .browse(vals.get("recipient_id", shipment.recipient_id.id))
                .exists()
            )
            vals = dict(vals)
            vals.update(self._snapshot_values(company, sender, recipient))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft_shipments(self):
        if any(shipment.state != "draft" for shipment in self):
            raise UserError(_("Only draft shipments can be deleted."))

    def _lock_shipments(self):
        shipments = self.exists()
        if not shipments:
            return shipments
        self.env.cr.execute(
            SQL(
                "SELECT id FROM parcel_shipment WHERE id IN %s ORDER BY id FOR UPDATE",
                tuple(sorted(shipments.ids)),
            )
        )
        shipments.invalidate_recordset(
            [
                "state",
                "courier_id",
                "package_ids",
                "expected_delivery_at",
                "original_expected_delivery_at",
                "first_picked_up_at",
                "transit_started_at",
                "delivered_at",
            ]
        )
        return shipments

    def _lock_packages(self):
        self.invalidate_recordset(["package_ids"])
        packages = self.mapped("package_ids").exists()
        if packages:
            self.env.cr.execute(
                SQL(
                    "SELECT id FROM parcel_package "
                    "WHERE id IN %s ORDER BY id FOR UPDATE",
                    tuple(sorted(packages.ids)),
                )
            )
            packages.invalidate_recordset(
                ["weight", "weight_uom_id", "pickup_event_id", "delivery_event_id"]
            )
        return packages

    def _lock_couriers(self, couriers):
        couriers = couriers.exists()
        if couriers:
            self.env.cr.execute(
                SQL(
                    "SELECT id FROM parcel_courier "
                    "WHERE id IN %s ORDER BY id FOR UPDATE",
                    tuple(sorted(couriers.ids)),
                )
            )
            # Odoo transactions use PostgreSQL repeatable-read snapshots.
            # Touching the locked rows turns concurrent reservations into a
            # serialization retry instead of allowing a stale capacity read.
            self.env.cr.execute(
                SQL(
                    "UPDATE parcel_courier SET write_date = write_date WHERE id IN %s",
                    tuple(sorted(couriers.ids)),
                )
            )
            couriers.invalidate_recordset(
                [
                    "availability",
                    "max_concurrent_shipments",
                    "max_concurrent_weight",
                    "max_weight_uom_id",
                    "zone_ids",
                ]
            )
        return couriers

    def _is_operator_or_manager(self):
        return (
            self.env.su
            or self.env.user.has_group("parcel_transport_management.group_ptm_operator")
            or self.env.user.has_group("parcel_transport_management.group_ptm_manager")
        )

    def _require_dispatch_access(self):
        if not self._is_operator_or_manager():
            raise AccessError(
                _("Only parcel operators or managers can dispatch shipments.")
            )

    def _require_operational_access(self):
        if self._is_operator_or_manager():
            return
        self.ensure_one()
        if self.courier_id.user_id != self.env.user:
            raise AccessError(_("Couriers can only operate their assigned shipments."))

    def _require_manager_access(self):
        if not self.env.su and not self.env.user.has_group(
            "parcel_transport_management.group_ptm_manager"
        ):
            raise AccessError(_("Only parcel managers can perform this operation."))

    def _base_coverage_warning(self):
        self.ensure_one()
        warnings = []
        if not self.origin_zone_id:
            warnings.append(
                _("The pickup address is outside configured zone coverage.")
            )
        if not self.destination_zone_id:
            warnings.append(
                _("The delivery address is outside configured zone coverage.")
            )
        return warnings

    def _coverage_warning_for_zones(self, origin_zone, destination_zone, courier=None):
        warnings = []
        if not origin_zone:
            warnings.append(
                _("The pickup address is outside configured zone coverage.")
            )
        if not destination_zone:
            warnings.append(
                _("The delivery address is outside configured zone coverage.")
            )
        if courier:
            shipment_zones = origin_zone | destination_zone
            if shipment_zones - courier.zone_ids:
                warnings.append(
                    _("The assigned courier does not cover every shipment zone.")
                )
        return "\n".join(warnings) or False

    def _coverage_warning_for(self, courier=None):
        self.ensure_one()
        return self._coverage_warning_for_zones(
            self.origin_zone_id,
            self.destination_zone_id,
            courier,
        )

    def _post_coverage_warning(self):
        for shipment in self.filtered("coverage_warning"):
            shipment.message_post(
                body=shipment.coverage_warning,
                subtype_xmlid="mail.mt_note",
            )

    def _shipment_weight_in_uom(self, shipment, target_uom):
        return sum(
            package.weight_uom_id._compute_quantity(
                package.weight, target_uom, round=False
            )
            for package in shipment.package_ids
        )

    def _check_courier_capacity(self, courier, candidates):
        if not courier.active:
            raise UserError(_("The selected courier is archived."))
        if courier.availability != "available":
            raise UserError(_("The selected courier is unavailable."))
        if not courier.max_weight_uom_id:
            raise UserError(_("The selected courier has no capacity weight unit."))

        reserved = self.search(
            [
                ("courier_id", "=", courier.id),
                ("state", "in", RESERVED_STATES),
                ("id", "not in", candidates.ids),
            ]
        )
        shipment_count = len(reserved) + len(candidates)
        if shipment_count > courier.max_concurrent_shipments:
            raise UserError(_("The selected courier has no shipment capacity."))

        total_weight = sum(
            self._shipment_weight_in_uom(shipment, courier.max_weight_uom_id)
            for shipment in reserved | candidates
        )
        if total_weight > courier.max_concurrent_weight:
            raise UserError(_("The selected courier has no weight capacity."))

    def action_assign(self, courier_id):
        self._require_dispatch_access()
        shipments = self._lock_shipments()
        packages = shipments._lock_packages()
        courier = self.env["parcel.courier"].browse(courier_id).exists()
        shipments._lock_couriers(courier)
        if not courier:
            raise UserError(_("A valid courier is required."))
        invalid = shipments.filtered(lambda shipment: shipment.state != "draft")
        if invalid:
            raise UserError(_("Only draft shipments can be assigned."))
        if any(not shipment.package_ids for shipment in shipments):
            raise UserError(_("A shipment must contain at least one package."))
        if any(shipment.company_id != courier.company_id for shipment in shipments):
            raise UserError(
                _("The courier and shipment must belong to the same company.")
            )
        self._check_courier_capacity(courier, shipments)

        now = fields.Datetime.now()
        for shipment in shipments:
            values = {
                "courier_id": courier.id,
                "state": "assigned",
                "coverage_warning": shipment._coverage_warning_for(courier),
            }
            if not shipment.original_expected_delivery_at:
                deadline = shipment.expected_delivery_at
                if not deadline and shipment.destination_zone_id:
                    deadline = now + timedelta(
                        hours=shipment.destination_zone_id.default_sla_hours
                    )
                if deadline:
                    values.update(
                        {
                            "expected_delivery_at": deadline,
                            "original_expected_delivery_at": deadline,
                        }
                    )
            super(ParcelShipment, shipment).write(values)
            shipment._post_coverage_warning()
        packages.invalidate_recordset(["shipment_id"])
        return True

    def action_unassign(self):
        self._require_dispatch_access()
        shipments = self._lock_shipments()
        shipments._lock_packages()
        couriers = shipments.mapped("courier_id")
        shipments._lock_couriers(couriers)
        invalid = shipments.filtered(lambda shipment: shipment.state != "assigned")
        if invalid:
            raise UserError(_("Only assigned shipments can be unassigned."))
        for shipment in shipments:
            super(ParcelShipment, shipment).write(
                {
                    "courier_id": False,
                    "state": "draft",
                    "coverage_warning": shipment._coverage_warning_for(),
                }
            )
        return True

    def action_reassign(self, courier_id, reason=None):
        self._require_dispatch_access()
        shipments = self._lock_shipments()
        shipments._lock_packages()
        new_courier = self.env["parcel.courier"].browse(courier_id).exists()
        couriers = shipments.mapped("courier_id") | new_courier
        shipments._lock_couriers(couriers)
        if not new_courier:
            raise UserError(_("A valid courier is required."))
        invalid = shipments.filtered(
            lambda shipment: shipment.state not in RESERVED_STATES
        )
        if invalid:
            raise UserError(_("Only active assigned shipments can be reassigned."))
        live_shipments = shipments.filtered(
            lambda shipment: shipment.state != "assigned"
        )
        if live_shipments:
            self._require_manager_access()
            if not reason or not reason.strip():
                raise UserError(
                    _("A reason is required to reassign a shipment after pickup.")
                )
        if any(shipment.company_id != new_courier.company_id for shipment in shipments):
            raise UserError(
                _("The courier and shipment must belong to the same company.")
            )
        self._check_courier_capacity(new_courier, shipments)
        normalized_reason = reason.strip() if reason and reason.strip() else False
        for shipment in shipments:
            previous_courier = shipment.courier_id
            if previous_courier == new_courier:
                continue
            super(ParcelShipment, shipment).write(
                {
                    "courier_id": new_courier.id,
                    "coverage_warning": shipment._coverage_warning_for(new_courier),
                }
            )
            self.env["parcel.courier.reassignment"].with_context(
                {
                    REASSIGNMENT_CREATE_CONTEXT: REASSIGNMENT_CREATE_TOKEN,
                }
            ).create(
                {
                    "shipment_id": shipment.id,
                    "previous_courier_id": previous_courier.id,
                    "new_courier_id": new_courier.id,
                    "reason": normalized_reason,
                }
            )
            shipment._post_coverage_warning()
        return True

    def action_revise_sla(self, expected_delivery_at, reason):
        self.ensure_one()
        self._require_manager_access()
        shipment = self._lock_shipments()
        if not reason or not reason.strip():
            raise UserError(_("An SLA revision reason is required."))
        if shipment.state in ("delivered", "cancelled"):
            raise UserError(_("A terminal shipment SLA cannot be revised."))
        if not shipment.expected_delivery_at:
            raise UserError(_("The shipment has no SLA to revise."))
        revised_at = fields.Datetime.to_datetime(expected_delivery_at)
        if not revised_at:
            raise UserError(_("A valid revised delivery deadline is required."))
        if revised_at == shipment.expected_delivery_at:
            raise UserError(_("The revised delivery deadline must be different."))
        self.env["parcel.sla.revision"].create(
            {
                "shipment_id": shipment.id,
                "previous_expected_delivery_at": shipment.expected_delivery_at,
                "new_expected_delivery_at": revised_at,
                "reason": reason.strip(),
            }
        )
        super(ParcelShipment, shipment).write({"expected_delivery_at": revised_at})
        return True

    def action_correct_route(self, corrected_values, reason):
        self.ensure_one()
        self._require_manager_access()
        shipment = self._lock_shipments()
        shipment._lock_couriers(shipment.courier_id)
        if not reason or not reason.strip():
            raise UserError(_("A route correction reason is required."))
        if not isinstance(corrected_values, dict) or not corrected_values:
            raise UserError(_("Provide at least one route field to correct."))
        unknown_fields = set(corrected_values) - set(ROUTE_SNAPSHOT_FIELDS)
        if unknown_fields:
            raise UserError(_("Only shipment route snapshot fields can be corrected."))

        normalized_values = dict(corrected_values)
        for field_name in ROUTE_COUNTRY_FIELDS.intersection(normalized_values):
            value = normalized_values[field_name]
            country_id = value.id if hasattr(value, "id") else value
            if country_id:
                country = self.env["res.country"].browse(country_id).exists()
                if not country:
                    raise UserError(_("A valid route country is required."))
                country_id = country.id
            normalized_values[field_name] = country_id or False

        previous_values = {}
        prospective_values = {}
        for field_name in ROUTE_SNAPSHOT_FIELDS:
            value = shipment[field_name]
            if field_name in ROUTE_COUNTRY_FIELDS:
                value = value.id or False
            prospective_values[field_name] = value
            if field_name in normalized_values:
                previous_values[field_name] = value
        prospective_values.update(normalized_values)

        rule_model = self.env["parcel.zone.postcode.rule"]
        origin_zone = rule_model._resolve(
            shipment.company_id,
            self.env["res.country"]
            .browse(prospective_values["pickup_country_id"])
            .exists(),
            prospective_values["pickup_zip"],
        )
        destination_zone = rule_model._resolve(
            shipment.company_id,
            self.env["res.country"]
            .browse(prospective_values["delivery_country_id"])
            .exists(),
            prospective_values["delivery_zip"],
        )
        previous_origin_zone = shipment.origin_zone_id
        previous_destination_zone = shipment.destination_zone_id
        applied = shipment.state not in ("delivered", "cancelled")
        if applied:
            route_values = dict(normalized_values)
            route_values.update(
                {
                    "origin_zone_id": origin_zone.id or False,
                    "destination_zone_id": destination_zone.id or False,
                    "coverage_warning": shipment._coverage_warning_for_zones(
                        origin_zone,
                        destination_zone,
                        shipment.courier_id,
                    ),
                }
            )
            super(ParcelShipment, shipment).write(route_values)
            shipment._post_coverage_warning()

        return self.env["parcel.route.correction"].create(
            {
                "shipment_id": shipment.id,
                "previous_values": previous_values,
                "new_values": normalized_values,
                "previous_origin_zone_id": previous_origin_zone.id or False,
                "new_origin_zone_id": origin_zone.id or False,
                "previous_destination_zone_id": previous_destination_zone.id or False,
                "new_destination_zone_id": destination_zone.id or False,
                "applied": applied,
                "reason": reason.strip(),
            }
        )

    def action_record_pickup(self, package_ids, note=None):
        self.ensure_one()
        shipment = self._lock_shipments()
        packages = shipment._lock_packages()
        courier = shipment.courier_id
        shipment._lock_couriers(courier)
        shipment._require_operational_access()
        if shipment.state not in ("assigned", "partially_picked_up"):
            raise UserError(_("Pickup can only be recorded for an assigned shipment."))
        if not courier:
            raise UserError(_("Pickup requires an assigned courier."))
        selected = packages.filtered(lambda package: package.id in set(package_ids))
        if not package_ids or len(selected) != len(set(package_ids)):
            raise UserError(_("Select packages belonging to this shipment."))
        if any(selected.mapped("pickup_event_id")):
            raise UserError(
                _("Pickup has already been recorded for a selected package.")
            )

        event = self.env["parcel.pickup.event"].create(
            {
                "shipment_id": shipment.id,
                "courier_id": courier.id,
                "note": note,
            }
        )
        selected._set_pickup_event(event)
        packages.invalidate_recordset(["pickup_event_id"])
        target_state = (
            "picked_up"
            if all(package.pickup_event_id for package in packages)
            else "partially_picked_up"
        )
        pickup_values = {"state": target_state}
        if not shipment.first_picked_up_at:
            pickup_values["first_picked_up_at"] = event.occurred_at
        super(ParcelShipment, shipment).write(pickup_values)
        return event

    def action_start_transit(self):
        self.ensure_one()
        shipment = self._lock_shipments()
        packages = shipment._lock_packages()
        courier = shipment.courier_id
        shipment._lock_couriers(courier)
        shipment._require_operational_access()
        if not courier:
            raise UserError(_("Transit requires an assigned courier."))
        if (
            shipment.state != "picked_up"
            or not packages
            or not all(package.pickup_event_id for package in packages)
        ):
            raise UserError(_("Every package must be picked up before transit starts."))
        super(ParcelShipment, shipment).write(
            {
                "state": "in_transit",
                "transit_started_at": fields.Datetime.now(),
            }
        )
        return True

    def action_record_delivery(self, package_ids, recipient_name=None, note=None):
        self.ensure_one()
        shipment = self._lock_shipments()
        packages = shipment._lock_packages()
        courier = shipment.courier_id
        shipment._lock_couriers(courier)
        shipment._require_operational_access()
        if shipment.state not in ("in_transit", "partially_delivered"):
            raise UserError(_("Delivery can only be recorded while in transit."))
        if not courier:
            raise UserError(_("Delivery requires an assigned courier."))
        if not recipient_name or not recipient_name.strip():
            raise UserError(_("The recipient name is required."))
        selected_ids = set(package_ids)
        selected = packages.filtered(lambda package: package.id in selected_ids)
        if not package_ids or len(selected) != len(selected_ids):
            raise UserError(_("Select packages belonging to this shipment."))
        if any(not package.pickup_event_id for package in selected):
            raise UserError(_("A package cannot be delivered before pickup."))
        if any(selected.mapped("delivery_event_id")):
            raise UserError(
                _("Delivery has already been recorded for a selected package.")
            )

        event = self.env["parcel.delivery.event"].create(
            {
                "shipment_id": shipment.id,
                "courier_id": courier.id,
                "recipient_name": recipient_name.strip(),
                "note": note,
            }
        )
        selected._set_delivery_event(event)
        packages.invalidate_recordset(["delivery_event_id"])
        target_state = (
            "delivered"
            if all(package.delivery_event_id for package in packages)
            else "partially_delivered"
        )
        delivery_values = {"state": target_state}
        if target_state == "delivered":
            delivery_values["delivered_at"] = event.occurred_at
        super(ParcelShipment, shipment).write(delivery_values)
        return event

    def action_cancel(self, reason):
        if not self.env.su and not self.env.user.has_group(
            "parcel_transport_management.group_ptm_manager"
        ):
            raise AccessError(_("Only parcel managers can cancel shipments."))
        shipments = self._lock_shipments()
        shipments._lock_packages()
        shipments._lock_couriers(shipments.mapped("courier_id"))
        invalid = shipments.filtered(
            lambda shipment: (
                shipment.state in ("delivered", "cancelled")
                or any(shipment.package_ids.mapped("delivery_event_id"))
            )
        )
        if invalid:
            raise UserError(_("Delivered or cancelled shipments cannot be cancelled."))
        if not reason or not reason.strip():
            raise UserError(_("A cancellation reason is required."))
        now = fields.Datetime.now()
        for shipment in shipments:
            super(ParcelShipment, shipment).write(
                {
                    "state": "cancelled",
                    "cancellation_reason": reason.strip(),
                    "cancelled_at": now,
                }
            )
        return True
