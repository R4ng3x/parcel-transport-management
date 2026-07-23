from odoo import api, fields, models

from .common import CLOSE_WIZARD_ACTION, active_shipment


class ParcelDeliveryWizard(models.TransientModel):
    _name = "parcel.delivery.wizard"
    _description = "Record Parcel Delivery"
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
    package_ids = fields.Many2many(
        "parcel.package",
        string="Packages to Deliver",
        required=True,
        check_company=True,
    )
    recipient_name = fields.Char(string="Recipient Name", required=True)
    note = fields.Text(string="Delivery Note")

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        shipment = active_shipment(self.env)
        values["shipment_id"] = shipment.id
        values["package_ids"] = [
            fields.Command.set(
                shipment.package_ids.filtered(
                    lambda package: (
                        package.pickup_event_id and not package.delivery_event_id
                    )
                ).ids
            )
        ]
        return values

    def action_confirm(self):
        self.ensure_one()
        self.shipment_id.action_record_delivery(
            self.package_ids.ids,
            self.recipient_name,
            self.note,
        )
        return CLOSE_WIZARD_ACTION
