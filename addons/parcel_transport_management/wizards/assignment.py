from odoo import api, fields, models

from .common import CLOSE_WIZARD_ACTION, active_shipment


class ParcelAssignmentWizard(models.TransientModel):
    _name = "parcel.assignment.wizard"
    _description = "Assign or Reassign Parcel Shipment"
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
    shipment_state = fields.Selection(
        related="shipment_id.state",
        readonly=True,
    )
    current_courier_id = fields.Many2one(
        "parcel.courier",
        string="Current Courier",
        related="shipment_id.courier_id",
        readonly=True,
    )
    courier_id = fields.Many2one(
        "parcel.courier",
        string="New Courier",
        required=True,
        ondelete="restrict",
        check_company=True,
    )
    reason = fields.Text(string="Reassignment Reason")

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        shipment = active_shipment(self.env)
        values["shipment_id"] = shipment.id
        return values

    def action_confirm(self):
        self.ensure_one()
        if self.shipment_id.state == "draft":
            self.shipment_id.action_assign(self.courier_id.id)
        else:
            self.shipment_id.action_reassign(self.courier_id.id, self.reason)
        return CLOSE_WIZARD_ACTION
