import math

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_RESERVED_SHIPMENT_STATES = (
    "assigned",
    "partially_picked_up",
    "picked_up",
    "in_transit",
    "partially_delivered",
)


class ParcelCourier(models.Model):
    _name = "parcel.courier"
    _description = "Parcel Courier"
    _order = "name, id"
    _check_company_auto = True

    name = fields.Char(required=True, index="trigram")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete="cascade",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Related User",
        check_company=True,
        index=True,
        ondelete="restrict",
    )
    availability = fields.Selection(
        [
            ("available", "Available"),
            ("unavailable", "Unavailable"),
        ],
        default="available",
        required=True,
        index=True,
    )
    max_concurrent_shipments = fields.Integer(
        string="Maximum Concurrent Shipments",
        required=True,
    )
    max_concurrent_weight = fields.Float(
        string="Maximum Concurrent Weight",
        required=True,
    )
    max_weight_uom_id = fields.Many2one(
        "uom.uom",
        string="Capacity Weight Unit",
        required=True,
        ondelete="restrict",
    )
    zone_ids = fields.Many2many(
        "parcel.delivery.zone",
        "parcel_courier_zone_rel",
        "courier_id",
        "zone_id",
        string="Delivery Zones",
        check_company=True,
    )
    shipment_ids = fields.One2many(
        "parcel.shipment",
        "courier_id",
        string="Shipments",
        readonly=True,
    )
    current_shipment_count = fields.Integer(
        string="Current Shipment Load",
        compute="_compute_current_load",
        compute_sudo=True,
    )
    current_weight = fields.Float(
        string="Current Weight Load",
        compute="_compute_current_load",
        compute_sudo=True,
    )
    current_weight_uom_id = fields.Many2one(
        related="max_weight_uom_id",
        string="Current Weight Unit",
    )

    _max_concurrent_shipments_positive = models.Constraint(
        "CHECK(max_concurrent_shipments > 0)",
        "The maximum concurrent shipment capacity must be strictly positive.",
    )
    _max_concurrent_weight_positive = models.Constraint(
        "CHECK(max_concurrent_weight > 0)",
        "The maximum concurrent weight must be strictly positive.",
    )
    _user_company_unique = models.Constraint(
        "UNIQUE(user_id, company_id)",
        "A user can be linked to only one courier per company.",
    )

    def _validate_max_concurrent_weight(self, value):
        if not math.isfinite(value) or value <= 0:
            raise ValidationError(
                _(
                    "The maximum concurrent weight must be a finite, strictly positive value."
                )
            )

    @api.model_create_multi
    def create(self, vals_list):
        company_ids = {
            values.get("company_id") or self.env.company.id for values in vals_list
        }
        if not company_ids.issubset(self.env.companies.ids):
            raise ValidationError(
                _(
                    "You cannot create a courier for a company outside your allowed companies."
                )
            )
        companies = {
            company.id: company
            for company in self.env["res.company"].browse(company_ids)
        }
        for values in vals_list:
            company = companies[values.get("company_id") or self.env.company.id]
            values.setdefault(
                "max_concurrent_shipments",
                company.parcel_default_courier_max_shipments,
            )
            values.setdefault(
                "max_concurrent_weight",
                company.parcel_default_courier_max_weight,
            )
            values.setdefault(
                "max_weight_uom_id",
                company.parcel_default_courier_weight_uom_id.id,
            )
            self._validate_max_concurrent_weight(values["max_concurrent_weight"])
        return super().create(vals_list)

    def write(self, values):
        if "company_id" in values and any(
            courier.company_id.id != values["company_id"] for courier in self
        ):
            raise ValidationError(
                _("A courier's company cannot be changed after creation.")
            )
        if "max_concurrent_weight" in values:
            self._validate_max_concurrent_weight(values["max_concurrent_weight"])
        return super().write(values)

    @api.constrains("max_concurrent_weight")
    def _check_max_concurrent_weight(self):
        for courier in self:
            self._validate_max_concurrent_weight(courier.max_concurrent_weight)

    @api.depends(
        "max_weight_uom_id",
        "shipment_ids.state",
        "shipment_ids.total_weight_kg",
    )
    def _compute_current_load(self):
        kilogram = self.env.ref("uom.product_uom_kgm")
        for courier in self:
            reserved_shipments = courier.shipment_ids.filtered(
                lambda shipment: shipment.state in _RESERVED_SHIPMENT_STATES
            )
            courier.current_shipment_count = len(reserved_shipments)
            total_weight_kg = sum(reserved_shipments.mapped("total_weight_kg"))
            courier.current_weight = kilogram._compute_quantity(
                total_weight_kg,
                courier.max_weight_uom_id,
                round=False,
            )

    @api.constrains("max_weight_uom_id")
    def _check_max_weight_uom(self):
        kilogram = self.env.ref("uom.product_uom_kgm")
        for courier in self:
            if not courier.max_weight_uom_id._has_common_reference(kilogram):
                raise ValidationError(
                    _("The courier capacity unit must be a weight unit.")
                )
