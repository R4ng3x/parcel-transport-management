from odoo import api, fields, models

from .common import CLOSE_WIZARD_ACTION, active_shipment


class ParcelSlaWizard(models.TransientModel):
    _name = "parcel.sla.wizard"
    _description = "Revise Parcel Shipment SLA"
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
    expected_delivery_at = fields.Datetime(
        string="Revised Delivery Deadline",
        required=True,
    )
    reason = fields.Text(string="Revision Reason", required=True)

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        shipment = active_shipment(self.env)
        values.update(
            {
                "shipment_id": shipment.id,
                "expected_delivery_at": shipment.expected_delivery_at,
            }
        )
        return values

    def action_confirm(self):
        self.ensure_one()
        self.shipment_id.action_revise_sla(
            self.expected_delivery_at,
            self.reason,
        )
        return CLOSE_WIZARD_ACTION
