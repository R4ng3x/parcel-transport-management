from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

REASSIGNMENT_CREATE_CONTEXT = "ptm_internal_courier_reassignment_create"
REASSIGNMENT_CREATE_TOKEN = object()


class ParcelCourierReassignment(models.Model):
    _name = "parcel.courier.reassignment"
    _description = "Parcel Courier Reassignment"
    _order = "occurred_at desc, id desc"
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
    previous_courier_id = fields.Many2one(
        "parcel.courier",
        required=True,
        readonly=True,
        ondelete="restrict",
        check_company=True,
        index=True,
    )
    new_courier_id = fields.Many2one(
        "parcel.courier",
        required=True,
        readonly=True,
        ondelete="restrict",
        check_company=True,
        index=True,
    )
    reason = fields.Text(readonly=True)
    changed_by_id = fields.Many2one(
        "res.users",
        string="Changed By",
        required=True,
        readonly=True,
        ondelete="restrict",
    )
    occurred_at = fields.Datetime(
        required=True,
        readonly=True,
        copy=False,
        index=True,
    )

    @api.private
    @api.model_create_multi
    def create(self, vals_list):
        if (
            self.env.context.get(REASSIGNMENT_CREATE_CONTEXT)
            is not REASSIGNMENT_CREATE_TOKEN
        ):
            raise AccessError(
                _(
                    "Courier reassignment history can only be recorded by reassignment actions."
                )
            )
        controlled = {"company_id", "changed_by_id", "occurred_at"}
        if any(controlled.intersection(values) for values in vals_list):
            raise ValidationError(
                _("Reassignment company, actor, and timestamp are server-controlled.")
            )

        occurred_at = fields.Datetime.now()
        prepared = []
        for incoming in vals_list:
            values = dict(incoming)
            shipment = (
                self.env["parcel.shipment"].browse(values.get("shipment_id")).exists()
            )
            previous_courier = (
                self.env["parcel.courier"]
                .browse(values.get("previous_courier_id"))
                .exists()
            )
            new_courier = (
                self.env["parcel.courier"].browse(values.get("new_courier_id")).exists()
            )
            if not shipment or not previous_courier or not new_courier:
                raise ValidationError(
                    _("A valid shipment and both couriers are required.")
                )
            if previous_courier == new_courier:
                raise ValidationError(
                    _("A reassignment must change the shipment courier.")
                )
            if (
                shipment.company_id != previous_courier.company_id
                or shipment.company_id != new_courier.company_id
            ):
                raise ValidationError(
                    _("The shipment and couriers must belong to the same company.")
                )
            reason = values.get("reason")
            values["reason"] = reason.strip() if reason and reason.strip() else False
            values["changed_by_id"] = self.env.user.id
            values["occurred_at"] = occurred_at
            prepared.append(values)
        return super().create(prepared)

    def write(self, vals):
        raise AccessError(
            _("Courier reassignment history is append-only and cannot be changed.")
        )

    def unlink(self):
        raise AccessError(
            _("Courier reassignment history is append-only and cannot be deleted.")
        )
