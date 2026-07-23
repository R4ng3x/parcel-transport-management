from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


def _event_user_is_authorized(env, shipment):
    if env.su:
        return True
    if env.user.has_group(
        "parcel_transport_management.group_ptm_operator"
    ) or env.user.has_group("parcel_transport_management.group_ptm_manager"):
        return True
    return shipment.courier_id.user_id == env.user


class ParcelPickupEvent(models.Model):
    _name = "parcel.pickup.event"
    _description = "Parcel Pickup Event"
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
    courier_id = fields.Many2one(
        "parcel.courier",
        required=True,
        readonly=True,
        ondelete="restrict",
        check_company=True,
        index=True,
    )
    confirmed_by_id = fields.Many2one(
        "res.users",
        string="Confirmed By",
        required=True,
        readonly=True,
        ondelete="restrict",
        default=lambda self: self.env.user,
    )
    occurred_at = fields.Datetime(
        required=True,
        readonly=True,
        copy=False,
        default=fields.Datetime.now,
        index=True,
    )
    note = fields.Text(readonly=True)
    package_ids = fields.One2many(
        "parcel.package",
        "pickup_event_id",
        string="Packages",
        readonly=True,
    )

    @api.depends("shipment_id.reference", "occurred_at")
    def _compute_display_name(self):
        for event in self:
            event.display_name = _(
                "Pickup %(shipment)s at %(timestamp)s (#%(event_id)s)",
                shipment=event.shipment_id.reference,
                timestamp=fields.Datetime.to_string(event.occurred_at),
                event_id=event.id,
            )

    @api.private
    @api.model_create_multi
    def create(self, vals_list):
        forbidden = {"company_id", "confirmed_by_id", "occurred_at", "package_ids"}
        if any(forbidden.intersection(values) for values in vals_list):
            raise ValidationError(
                _("Pickup event actor, timestamp, and packages are server-controlled.")
            )
        shipments = (
            self.env["parcel.shipment"]
            .browse({values.get("shipment_id") for values in vals_list})
            .exists()
        )
        couriers = (
            self.env["parcel.courier"]
            .browse({values.get("courier_id") for values in vals_list})
            .exists()
        )
        shipments._lock_shipments()
        shipments._lock_packages()
        shipments._lock_couriers(couriers | shipments.mapped("courier_id"))
        for values in vals_list:
            shipment = (
                self.env["parcel.shipment"].browse(values.get("shipment_id")).exists()
            )
            courier = (
                self.env["parcel.courier"].browse(values.get("courier_id")).exists()
            )
            if not shipment or not courier:
                raise ValidationError(_("A shipment and courier are required."))
            if shipment.state not in ("assigned", "partially_picked_up"):
                raise UserError(_("Pickup events require an assigned shipment."))
            if (
                shipment.courier_id != courier
                or shipment.company_id != courier.company_id
            ):
                raise ValidationError(
                    _("The event courier must be assigned to the shipment.")
                )
            if not _event_user_is_authorized(self.env, shipment):
                raise AccessError(
                    _("You cannot record events for another courier's shipment.")
                )
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_("Pickup events are append-only and cannot be changed."))

    def unlink(self):
        raise AccessError(_("Pickup events are append-only and cannot be deleted."))


class ParcelDeliveryEvent(models.Model):
    _name = "parcel.delivery.event"
    _description = "Parcel Delivery Event"
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
    courier_id = fields.Many2one(
        "parcel.courier",
        required=True,
        readonly=True,
        ondelete="restrict",
        check_company=True,
        index=True,
    )
    confirmed_by_id = fields.Many2one(
        "res.users",
        string="Confirmed By",
        required=True,
        readonly=True,
        ondelete="restrict",
        default=lambda self: self.env.user,
    )
    occurred_at = fields.Datetime(
        required=True,
        readonly=True,
        copy=False,
        default=fields.Datetime.now,
        index=True,
    )
    recipient_name = fields.Char(required=True, readonly=True)
    note = fields.Text(readonly=True)
    package_ids = fields.One2many(
        "parcel.package",
        "delivery_event_id",
        string="Packages",
        readonly=True,
    )

    @api.depends("shipment_id.reference", "occurred_at")
    def _compute_display_name(self):
        for event in self:
            event.display_name = _(
                "Delivery %(shipment)s at %(timestamp)s (#%(event_id)s)",
                shipment=event.shipment_id.reference,
                timestamp=fields.Datetime.to_string(event.occurred_at),
                event_id=event.id,
            )

    @api.private
    @api.model_create_multi
    def create(self, vals_list):
        forbidden = {"company_id", "confirmed_by_id", "occurred_at", "package_ids"}
        if any(forbidden.intersection(values) for values in vals_list):
            raise ValidationError(
                _(
                    "Delivery event actor, timestamp, and packages are server-controlled."
                )
            )
        shipments = (
            self.env["parcel.shipment"]
            .browse({values.get("shipment_id") for values in vals_list})
            .exists()
        )
        couriers = (
            self.env["parcel.courier"]
            .browse({values.get("courier_id") for values in vals_list})
            .exists()
        )
        shipments._lock_shipments()
        shipments._lock_packages()
        shipments._lock_couriers(couriers | shipments.mapped("courier_id"))
        for values in vals_list:
            shipment = (
                self.env["parcel.shipment"].browse(values.get("shipment_id")).exists()
            )
            courier = (
                self.env["parcel.courier"].browse(values.get("courier_id")).exists()
            )
            if not shipment or not courier:
                raise ValidationError(_("A shipment and courier are required."))
            if shipment.state not in ("in_transit", "partially_delivered"):
                raise UserError(_("Delivery events require a shipment in transit."))
            if (
                shipment.courier_id != courier
                or shipment.company_id != courier.company_id
            ):
                raise ValidationError(
                    _("The event courier must be assigned to the shipment.")
                )
            recipient_name = values.get("recipient_name")
            if not recipient_name or not recipient_name.strip():
                raise ValidationError(_("The recipient name is required."))
            values["recipient_name"] = recipient_name.strip()
            if not _event_user_is_authorized(self.env, shipment):
                raise AccessError(
                    _("You cannot record events for another courier's shipment.")
                )
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_("Delivery events are append-only and cannot be changed."))

    def unlink(self):
        raise AccessError(_("Delivery events are append-only and cannot be deleted."))
