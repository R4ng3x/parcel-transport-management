import math
import re
from secrets import choice

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

TRACKING_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
TRACKING_PAYLOAD_LENGTH = 16
_TRACKING_CANONICAL_RE = re.compile(
    rf"PTM-(?:[{TRACKING_ALPHABET}]{{4}}-){{3}}[{TRACKING_ALPHABET}]{{4}}"
)
_TRACKING_COMPACT_RE = re.compile(
    rf"PTM[{TRACKING_ALPHABET}]{{{TRACKING_PAYLOAD_LENGTH}}}"
)


def _is_strictly_positive_finite(value):
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric_value) and numeric_value > 0


class ParcelPackage(models.Model):
    _name = "parcel.package"
    _description = "Parcel Package"
    _rec_name = "tracking_code"
    _order = "id"
    _check_company_auto = True

    shipment_id = fields.Many2one(
        "parcel.shipment",
        required=True,
        ondelete="cascade",
        check_company=True,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="shipment_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )
    tracking_code = fields.Char(
        required=True,
        readonly=True,
        copy=False,
        index=True,
    )
    weight = fields.Float(required=True, digits=(16, 6))
    weight_uom_id = fields.Many2one(
        "uom.uom",
        string="Weight Unit",
        required=True,
        ondelete="restrict",
        default=lambda self: self.env.company.parcel_max_package_weight_uom_id,
    )
    weight_kg = fields.Float(
        compute="_compute_weight_kg",
        string="Weight (kg)",
        digits=(16, 6),
        store=True,
    )
    pickup_event_id = fields.Many2one(
        "parcel.pickup.event",
        readonly=True,
        copy=False,
        ondelete="restrict",
        check_company=True,
        index=True,
    )
    delivery_event_id = fields.Many2one(
        "parcel.delivery.event",
        readonly=True,
        copy=False,
        ondelete="restrict",
        check_company=True,
        index=True,
    )
    delivery_attempt_ids = fields.Many2many(
        "parcel.delivery.attempt",
        "parcel_delivery_attempt_package_rel",
        "package_id",
        "attempt_id",
        string="Delivery Attempts",
        readonly=True,
        copy=False,
        check_company=True,
    )

    _tracking_code_unique = models.Constraint(
        "UNIQUE(tracking_code)", "Package tracking codes must be globally unique."
    )
    _weight_positive = models.Constraint(
        "CHECK(weight > 0)", "Package weight must be strictly positive."
    )

    @api.depends("weight", "weight_uom_id")
    def _compute_weight_kg(self):
        kg_uom = self.env.ref("uom.product_uom_kgm")
        for package in self:
            package.weight_kg = (
                package.weight_uom_id._compute_quantity(
                    package.weight, kg_uom, round=False
                )
                if package.weight_uom_id
                else 0.0
            )

    @api.model
    def _new_tracking_code(self):
        while True:
            payload = "".join(
                choice(TRACKING_ALPHABET) for _index in range(TRACKING_PAYLOAD_LENGTH)
            )
            code = "PTM-" + "-".join(
                payload[index : index + 4] for index in range(0, 16, 4)
            )
            if not self.search_count([("tracking_code", "=", code)], limit=1):
                return code

    @api.constrains("weight", "weight_uom_id", "company_id")
    def _check_weight_configuration(self):
        kg_uom = self.env.ref("uom.product_uom_kgm")
        for package in self:
            if not _is_strictly_positive_finite(package.weight):
                raise ValidationError(_("Package weight must be strictly positive."))
            if not package.weight_uom_id or not (
                package.weight_uom_id._has_common_reference(kg_uom)
            ):
                raise ValidationError(_("Package weight must use a weight unit."))
            maximum = package.company_id.parcel_max_package_weight
            maximum_uom = package.company_id.parcel_max_package_weight_uom_id
            if not maximum_uom:
                raise ValidationError(
                    _("The company maximum package weight unit is not configured.")
                )
            converted = package.weight_uom_id._compute_quantity(
                package.weight, maximum_uom, round=False
            )
            if converted > maximum:
                raise ValidationError(
                    _(
                        "Package weight exceeds the company maximum of %(maximum)s %(unit)s."
                    )
                    % {
                        "maximum": maximum,
                        "unit": maximum_uom.display_name,
                    }
                )

    @api.model_create_multi
    def create(self, vals_list):
        shipment_ids = {
            values.get("shipment_id")
            for values in vals_list
            if values.get("shipment_id")
        }
        shipments = self.env["parcel.shipment"].browse(shipment_ids).exists()
        shipments._lock_shipments()
        if len(shipments) != len(shipment_ids):
            raise ValidationError(_("A valid shipment is required."))
        if any(shipment.state != "draft" for shipment in shipments):
            raise UserError(_("Packages can only be added to draft shipments."))

        prepared = []
        for incoming in vals_list:
            values = dict(incoming)
            if not _is_strictly_positive_finite(values.get("weight", 0.0)):
                raise ValidationError(_("Package weight must be strictly positive."))
            if "tracking_code" in values:
                raise ValidationError(_("Tracking codes are generated by the server."))
            if {
                "pickup_event_id",
                "delivery_event_id",
                "delivery_attempt_ids",
            }.intersection(values):
                raise AccessError(_("Package events cannot be set directly."))
            values["tracking_code"] = self._new_tracking_code()
            prepared.append(values)
        return super().create(prepared)

    def write(self, vals):
        if "tracking_code" in vals:
            raise ValidationError(_("Tracking codes are generated by the server."))
        if "weight" in vals and not _is_strictly_positive_finite(vals["weight"]):
            raise ValidationError(_("Package weight must be strictly positive."))
        if {
            "shipment_id",
            "company_id",
            "pickup_event_id",
            "delivery_event_id",
            "delivery_attempt_ids",
        }.intersection(vals):
            raise AccessError(_("Protected package fields cannot be written directly."))
        shipments = self.mapped("shipment_id")._lock_shipments()
        shipments._lock_packages()
        if any(shipment.state != "draft" for shipment in shipments):
            raise UserError(_("Packages can only be changed on draft shipments."))
        return super().write(vals)

    def unlink(self):
        shipments = self.mapped("shipment_id")._lock_shipments()
        shipments._lock_packages()
        if any(shipment.state != "draft" for shipment in shipments):
            raise UserError(_("Packages can only be removed from draft shipments."))
        return super().unlink()

    def _set_pickup_event(self, event):
        return super().write({"pickup_event_id": event.id})

    def _set_delivery_event(self, event):
        return super().write({"delivery_event_id": event.id})

    @api.model
    def _normalize_tracking_code(self, tracking_code):
        if not isinstance(tracking_code, str):
            return False
        candidate = tracking_code.strip().upper()
        if _TRACKING_CANONICAL_RE.fullmatch(candidate):
            return candidate
        if not _TRACKING_COMPACT_RE.fullmatch(candidate):
            return False
        payload = candidate[3:]
        return "PTM-" + "-".join(
            payload[index : index + 4] for index in range(0, TRACKING_PAYLOAD_LENGTH, 4)
        )

    def get_public_tracking_data(self):
        self.ensure_one()
        shipment = self.shipment_id
        pickup = self.pickup_event_id
        delivery = self.delivery_event_id

        if delivery:
            current_status = "delivered"
        elif shipment.state == "cancelled":
            current_status = "cancelled"
        elif shipment.state == "delivery_failed":
            current_status = "delivery_failed"
        elif pickup:
            current_status = (
                "in_transit"
                if shipment.state in ("in_transit", "partially_delivered", "delivered")
                else "picked_up"
            )
        elif shipment.state == "draft":
            current_status = "draft"
        else:
            current_status = "assigned"

        timeline = []

        def add_timeline_item(status, occurred_at):
            if occurred_at:
                timeline.append(
                    {
                        "status": status,
                        "occurred_at": fields.Datetime.to_string(occurred_at),
                    }
                )

        add_timeline_item("draft", self.create_date)
        if shipment.state == "assigned":
            add_timeline_item("assigned", shipment.write_date)
        add_timeline_item("picked_up", pickup.occurred_at if pickup else False)
        if pickup and shipment.transit_started_at:
            add_timeline_item("in_transit", shipment.transit_started_at)
        for attempt in self.delivery_attempt_ids:
            add_timeline_item("delivery_failed", attempt.occurred_at)
            for retry in attempt.retry_ids:
                add_timeline_item("in_transit", retry.occurred_at)
        add_timeline_item("delivered", delivery.occurred_at if delivery else False)
        if not delivery and shipment.state == "cancelled":
            add_timeline_item("cancelled", shipment.cancelled_at)
        timeline.sort(key=lambda item: item["occurred_at"])

        return {
            "tracking_code": self.tracking_code,
            "current_status": current_status,
            "expected_delivery_at": (
                fields.Datetime.to_string(shipment.expected_delivery_at)
                if shipment.expected_delivery_at
                else None
            ),
            "last_updated_at": (timeline[-1]["occurred_at"] if timeline else None),
            "timeline": timeline,
        }
