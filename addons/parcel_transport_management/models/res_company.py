import math

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = "res.company"

    parcel_max_package_weight = fields.Float(
        string="Maximum Package Weight",
        default=30.0,
        required=True,
    )
    parcel_max_package_weight_uom_id = fields.Many2one(
        "uom.uom",
        string="Maximum Package Weight Unit",
        default=lambda self: self.env.ref("uom.product_uom_kgm"),
        required=True,
        ondelete="restrict",
    )
    parcel_default_courier_max_shipments = fields.Integer(
        string="Default Courier Shipment Capacity",
        default=8,
        required=True,
    )
    parcel_default_courier_max_weight = fields.Float(
        string="Default Courier Weight Capacity",
        default=150.0,
        required=True,
    )
    parcel_default_courier_weight_uom_id = fields.Many2one(
        "uom.uom",
        string="Default Courier Weight Unit",
        default=lambda self: self.env.ref("uom.product_uom_kgm"),
        required=True,
        ondelete="restrict",
    )

    _parcel_max_package_weight_positive = models.Constraint(
        "CHECK(parcel_max_package_weight > 0)",
        "The maximum package weight must be strictly positive.",
    )
    _parcel_default_courier_max_shipments_positive = models.Constraint(
        "CHECK(parcel_default_courier_max_shipments > 0)",
        "The default courier shipment capacity must be strictly positive.",
    )
    _parcel_default_courier_max_weight_positive = models.Constraint(
        "CHECK(parcel_default_courier_max_weight > 0)",
        "The default courier weight capacity must be strictly positive.",
    )

    def _validate_max_package_weight(self, value):
        if not math.isfinite(value) or value <= 0:
            raise ValidationError(
                _(
                    "The maximum package weight must be a finite, strictly positive value."
                )
            )

    def _validate_default_courier_max_weight(self, value):
        if not math.isfinite(value) or value <= 0:
            raise ValidationError(
                _(
                    "The default courier weight capacity must be a finite, strictly positive value."
                )
            )

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if "parcel_max_package_weight" in values:
                self._validate_max_package_weight(values["parcel_max_package_weight"])
            if "parcel_default_courier_max_weight" in values:
                self._validate_default_courier_max_weight(
                    values["parcel_default_courier_max_weight"]
                )
        return super().create(vals_list)

    def _validate_operational_package_limits(self, values):
        limit_fields = {
            "parcel_max_package_weight",
            "parcel_max_package_weight_uom_id",
        }
        if not limit_fields.intersection(values):
            return

        shipments = (
            self.env["parcel.shipment"]
            .sudo()
            .search(
                [
                    ("company_id", "in", self.ids),
                    ("state", "not in", ("delivered", "cancelled")),
                ],
                order="id",
            )
        )
        shipments = shipments._lock_shipments().filtered(
            lambda shipment: shipment.state not in ("delivered", "cancelled")
        )
        packages = shipments._lock_packages()
        for company in self:
            company_packages = packages.filtered(
                lambda package, company=company: package.company_id.id == company.id
            )
            if not company_packages:
                continue
            maximum = values.get(
                "parcel_max_package_weight",
                company.parcel_max_package_weight,
            )
            maximum_uom_id = values.get(
                "parcel_max_package_weight_uom_id",
                company.parcel_max_package_weight_uom_id.id,
            )
            maximum_uom = self.env["uom.uom"].sudo().browse(maximum_uom_id).exists()
            company_packages._validate_weight_limit(maximum, maximum_uom)

    def write(self, values):
        if "parcel_max_package_weight" in values:
            self._validate_max_package_weight(values["parcel_max_package_weight"])
        if "parcel_default_courier_max_weight" in values:
            self._validate_default_courier_max_weight(
                values["parcel_default_courier_max_weight"]
            )
        self._validate_operational_package_limits(values)
        return super().write(values)

    @api.constrains(
        "parcel_max_package_weight",
        "parcel_default_courier_max_weight",
    )
    def _check_parcel_weight_limits(self):
        for company in self:
            self._validate_max_package_weight(company.parcel_max_package_weight)
            self._validate_default_courier_max_weight(
                company.parcel_default_courier_max_weight
            )

    @api.constrains(
        "parcel_max_package_weight_uom_id",
        "parcel_default_courier_weight_uom_id",
    )
    def _check_parcel_weight_uoms(self):
        kilogram = self.env.ref("uom.product_uom_kgm")
        for company in self:
            if not company.parcel_max_package_weight_uom_id._has_common_reference(
                kilogram
            ):
                raise ValidationError(
                    _("The maximum package weight unit must be a weight unit.")
                )
            if not company.parcel_default_courier_weight_uom_id._has_common_reference(
                kilogram
            ):
                raise ValidationError(
                    _("The default courier capacity unit must be a weight unit.")
                )


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    parcel_max_package_weight = fields.Float(
        related="company_id.parcel_max_package_weight",
        readonly=False,
    )
    parcel_max_package_weight_uom_id = fields.Many2one(
        related="company_id.parcel_max_package_weight_uom_id",
        readonly=False,
    )
    parcel_default_courier_max_shipments = fields.Integer(
        related="company_id.parcel_default_courier_max_shipments",
        readonly=False,
    )
    parcel_default_courier_max_weight = fields.Float(
        related="company_id.parcel_default_courier_max_weight",
        readonly=False,
    )
    parcel_default_courier_weight_uom_id = fields.Many2one(
        related="company_id.parcel_default_courier_weight_uom_id",
        readonly=False,
    )
