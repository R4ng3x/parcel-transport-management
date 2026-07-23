from odoo import _
from odoo.exceptions import UserError

CLOSE_WIZARD_ACTION = {"type": "ir.actions.act_window_close"}


def active_shipment(env):
    context = env.context
    active_id = context.get("active_id")
    if context.get("active_model") != "parcel.shipment" or not active_id:
        raise UserError(_("Open this assistant from a shipment."))

    shipment = env["parcel.shipment"].browse(active_id).exists()
    if not shipment:
        raise UserError(_("The selected shipment no longer exists."))
    return shipment
