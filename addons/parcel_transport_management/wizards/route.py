from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .common import CLOSE_WIZARD_ACTION, active_shipment

ROUTE_FIELD_NAMES = (
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
COUNTRY_FIELD_NAMES = {"pickup_country_id", "delivery_country_id"}


class ParcelRouteWizard(models.TransientModel):
    _name = "parcel.route.wizard"
    _description = "Correct Parcel Shipment Route"
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
    pickup_name = fields.Char(string="Pickup Name")
    pickup_street = fields.Char(string="Pickup Street")
    pickup_street2 = fields.Char(string="Pickup Street 2")
    pickup_city = fields.Char(string="Pickup City")
    pickup_zip = fields.Char(string="Pickup ZIP")
    pickup_country_id = fields.Many2one(
        "res.country",
        string="Pickup Country",
        ondelete="restrict",
    )
    delivery_name = fields.Char(string="Delivery Name")
    delivery_street = fields.Char(string="Delivery Street")
    delivery_street2 = fields.Char(string="Delivery Street 2")
    delivery_city = fields.Char(string="Delivery City")
    delivery_zip = fields.Char(string="Delivery ZIP")
    delivery_country_id = fields.Many2one(
        "res.country",
        string="Delivery Country",
        ondelete="restrict",
    )
    reason = fields.Text(string="Correction Reason", required=True)

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        shipment = active_shipment(self.env)
        values["shipment_id"] = shipment.id
        for field_name in ROUTE_FIELD_NAMES:
            value = shipment[field_name]
            values[field_name] = (
                value.id if field_name in COUNTRY_FIELD_NAMES else value
            )
        return values

    def action_confirm(self):
        self.ensure_one()
        corrected_values = {}
        for field_name in ROUTE_FIELD_NAMES:
            current_value = self.shipment_id[field_name]
            new_value = self[field_name]
            if field_name in COUNTRY_FIELD_NAMES:
                current_value = current_value.id or False
                new_value = new_value.id or False
            if new_value != current_value:
                corrected_values[field_name] = new_value
        if not corrected_values:
            raise UserError(_("Change at least one route field."))

        correction = self.shipment_id.action_correct_route(
            corrected_values,
            self.reason,
        )
        if correction.applied:
            title = _("Route Correction Applied")
            message = _(
                "The route snapshot was updated and the correction was recorded."
            )
            notification_type = "success"
        else:
            title = _("Audit Annotation Recorded")
            message = _(
                "The terminal shipment snapshot was not changed; "
                "the correction was recorded for audit only."
            )
            notification_type = "warning"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": notification_type,
                "sticky": False,
                "next": CLOSE_WIZARD_ACTION,
            },
        }
