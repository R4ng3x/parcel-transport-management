from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class ParcelSlaRevision(models.Model):
    _name = "parcel.sla.revision"
    _description = "Parcel SLA Revision"
    _order = "changed_at desc, id desc"
    _check_company_auto = True

    shipment_id = fields.Many2one(
        "parcel.shipment",
        required=True,
        readonly=True,
        ondelete="restrict",
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
    previous_expected_delivery_at = fields.Datetime(required=True, readonly=True)
    new_expected_delivery_at = fields.Datetime(required=True, readonly=True)
    reason = fields.Text(required=True, readonly=True)
    changed_by_id = fields.Many2one(
        "res.users",
        string="Changed By",
        required=True,
        readonly=True,
        ondelete="restrict",
        default=lambda self: self.env.user,
    )
    changed_at = fields.Datetime(
        required=True,
        readonly=True,
        copy=False,
        default=fields.Datetime.now,
        index=True,
    )

    @api.private
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and not self.env.user.has_group(
            "parcel_transport_management.group_ptm_manager"
        ):
            raise AccessError(_("Only parcel managers can revise shipment SLAs."))
        forbidden = {"company_id", "changed_by_id", "changed_at"}
        if any(forbidden.intersection(values) for values in vals_list):
            raise ValidationError(
                _("SLA revision company, actor, and timestamp are server-controlled.")
            )
        for values in vals_list:
            shipment = (
                self.env["parcel.shipment"].browse(values.get("shipment_id")).exists()
            )
            reason = values.get("reason")
            if not shipment:
                raise ValidationError(_("A valid shipment is required."))
            if not reason or not reason.strip():
                raise ValidationError(_("An SLA revision reason is required."))
            values["reason"] = reason.strip()
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_("SLA revisions are append-only and cannot be changed."))

    def unlink(self):
        raise AccessError(_("SLA revisions are append-only and cannot be deleted."))


class ParcelRouteCorrection(models.Model):
    _name = "parcel.route.correction"
    _description = "Parcel Route Correction"
    _order = "changed_at desc, id desc"
    _check_company_auto = True

    shipment_id = fields.Many2one(
        "parcel.shipment",
        required=True,
        readonly=True,
        ondelete="restrict",
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
    previous_values = fields.Json(required=True, readonly=True)
    new_values = fields.Json(required=True, readonly=True)
    previous_origin_zone_id = fields.Many2one(
        "parcel.delivery.zone",
        readonly=True,
        ondelete="restrict",
        check_company=True,
    )
    new_origin_zone_id = fields.Many2one(
        "parcel.delivery.zone",
        readonly=True,
        ondelete="restrict",
        check_company=True,
    )
    previous_destination_zone_id = fields.Many2one(
        "parcel.delivery.zone",
        readonly=True,
        ondelete="restrict",
        check_company=True,
    )
    new_destination_zone_id = fields.Many2one(
        "parcel.delivery.zone",
        readonly=True,
        ondelete="restrict",
        check_company=True,
    )
    applied = fields.Boolean(required=True, readonly=True, default=False)
    reason = fields.Text(required=True, readonly=True)
    changed_by_id = fields.Many2one(
        "res.users",
        string="Changed By",
        required=True,
        readonly=True,
        ondelete="restrict",
        default=lambda self: self.env.user,
    )
    changed_at = fields.Datetime(
        required=True,
        readonly=True,
        copy=False,
        default=fields.Datetime.now,
        index=True,
    )

    @api.private
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and not self.env.user.has_group(
            "parcel_transport_management.group_ptm_manager"
        ):
            raise AccessError(_("Only parcel managers can correct shipment routes."))
        forbidden = {"company_id", "changed_by_id", "changed_at"}
        if any(forbidden.intersection(values) for values in vals_list):
            raise ValidationError(
                _(
                    "Route correction company, actor, and timestamp are server-controlled."
                )
            )
        for values in vals_list:
            shipment = (
                self.env["parcel.shipment"].browse(values.get("shipment_id")).exists()
            )
            reason = values.get("reason")
            if not shipment:
                raise ValidationError(_("A valid shipment is required."))
            if not reason or not reason.strip():
                raise ValidationError(_("A route correction reason is required."))
            values["reason"] = reason.strip()
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_("Route corrections are append-only and cannot be changed."))

    def unlink(self):
        raise AccessError(_("Route corrections are append-only and cannot be deleted."))
