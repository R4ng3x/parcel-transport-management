from odoo import api, fields, models

from .common import CLOSE_WIZARD_ACTION, active_shipment


class ParcelCancelWizard(models.TransientModel):
    _name = "parcel.cancel.wizard"
    _description = "Cancel Parcel Shipment"
    _check_company_auto = True

    shipment_id = fields.Many2one(
        "parcel.shipment",
        string="Shipment",
        required=True,
        readonly=True,
        ondelete="cascade",
        check_company=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="shipment_id.company_id",
        readonly=True,
    )
    reason = fields.Text(string="Cancellation Reason", required=True)

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        values["shipment_id"] = active_shipment(self.env).id
        return values

    def action_confirm(self):
        self.ensure_one()
        self.shipment_id.action_cancel(self.reason)
        return CLOSE_WIZARD_ACTION
