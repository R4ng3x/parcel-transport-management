from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

DELIVERY_ATTEMPT_CREATE_CONTEXT = "ptm_internal_delivery_attempt_create"
DELIVERY_ATTEMPT_CREATE_TOKEN = object()
DELIVERY_RETRY_CREATE_CONTEXT = "ptm_internal_delivery_retry_create"
DELIVERY_RETRY_CREATE_TOKEN = object()


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


class ParcelDeliveryAttempt(models.Model):
    _name = "parcel.delivery.attempt"
    _description = "Parcel Delivery Attempt"
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
    )
    occurred_at = fields.Datetime(
        required=True,
        readonly=True,
        copy=False,
        index=True,
    )
    reason = fields.Text(required=True, readonly=True)
    package_ids = fields.Many2many(
        "parcel.package",
        "parcel_delivery_attempt_package_rel",
        "attempt_id",
        "package_id",
        string="Packages",
        required=True,
        readonly=True,
        check_company=True,
    )
    retry_ids = fields.One2many(
        "parcel.delivery.retry",
        "attempt_id",
        string="Retries",
        readonly=True,
    )

    @api.depends("shipment_id.reference", "occurred_at")
    def _compute_display_name(self):
        for attempt in self:
            attempt.display_name = _(
                "Failed delivery %(shipment)s at %(timestamp)s (#%(attempt_id)s)",
                shipment=attempt.shipment_id.reference,
                timestamp=fields.Datetime.to_string(attempt.occurred_at),
                attempt_id=attempt.id,
            )

    @api.private
    @api.model_create_multi
    def create(self, vals_list):
        if (
            self.env.context.get(DELIVERY_ATTEMPT_CREATE_CONTEXT)
            is not DELIVERY_ATTEMPT_CREATE_TOKEN
        ):
            raise AccessError(
                _("Delivery attempts can only be recorded by delivery failure actions.")
            )
        controlled = {
            "company_id",
            "confirmed_by_id",
            "occurred_at",
            "package_ids",
            "retry_ids",
        }
        if any(controlled.intersection(values) for values in vals_list):
            raise ValidationError(
                _(
                    "Delivery attempt company, actor, timestamp, packages, and retries are server-controlled."
                )
            )

        occurred_at = fields.Datetime.now()
        prepared = []
        for incoming in vals_list:
            values = dict(incoming)
            shipment = (
                self.env["parcel.shipment"].browse(values.get("shipment_id")).exists()
            )
            courier = (
                self.env["parcel.courier"].browse(values.get("courier_id")).exists()
            )
            if not shipment or not courier:
                raise ValidationError(_("A valid shipment and courier are required."))
            if shipment.state not in ("in_transit", "partially_delivered"):
                raise UserError(
                    _("A failed delivery requires a shipment currently in transit.")
                )
            if (
                shipment.courier_id != courier
                or shipment.company_id != courier.company_id
            ):
                raise ValidationError(
                    _("The attempt courier must be assigned to the shipment.")
                )
            reason = values.get("reason")
            if not reason or not reason.strip():
                raise ValidationError(_("A delivery failure reason is required."))
            packages = shipment.package_ids.filtered(
                lambda package: not package.delivery_event_id
            )
            if not packages or any(not package.pickup_event_id for package in packages):
                raise UserError(
                    _(
                        "A failed delivery requires at least one undelivered picked-up package."
                    )
                )
            if not _event_user_is_authorized(self.env, shipment):
                raise AccessError(
                    _("You cannot record events for another courier's shipment.")
                )
            values.update(
                {
                    "reason": reason.strip(),
                    "confirmed_by_id": self.env.user.id,
                    "occurred_at": occurred_at,
                    "package_ids": [fields.Command.set(packages.ids)],
                }
            )
            prepared.append(values)
        return super().create(prepared)

    def write(self, vals):
        raise AccessError(_("Delivery attempts are append-only and cannot be changed."))

    def unlink(self):
        raise AccessError(_("Delivery attempts are append-only and cannot be deleted."))


class ParcelDeliveryRetry(models.Model):
    _name = "parcel.delivery.retry"
    _description = "Parcel Delivery Retry"
    _order = "occurred_at desc, id desc"
    _check_company_auto = True

    attempt_id = fields.Many2one(
        "parcel.delivery.attempt",
        required=True,
        readonly=True,
        ondelete="restrict",
        check_company=True,
        index=True,
    )
    shipment_id = fields.Many2one(
        "parcel.shipment",
        related="attempt_id.shipment_id",
        store=True,
        readonly=True,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="attempt_id.company_id",
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
    dispatched_by_id = fields.Many2one(
        "res.users",
        string="Dispatched By",
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
    reason = fields.Text(required=True, readonly=True)

    _attempt_unique = models.Constraint(
        "UNIQUE(attempt_id)", "A delivery attempt can only be retried once."
    )

    @api.depends("shipment_id.reference", "occurred_at")
    def _compute_display_name(self):
        for retry in self:
            retry.display_name = _(
                "Delivery retry %(shipment)s at %(timestamp)s (#%(retry_id)s)",
                shipment=retry.shipment_id.reference,
                timestamp=fields.Datetime.to_string(retry.occurred_at),
                retry_id=retry.id,
            )

    @api.private
    @api.model_create_multi
    def create(self, vals_list):
        if (
            self.env.context.get(DELIVERY_RETRY_CREATE_CONTEXT)
            is not DELIVERY_RETRY_CREATE_TOKEN
        ):
            raise AccessError(
                _("Delivery retries can only be recorded by retry actions.")
            )
        controlled = {
            "shipment_id",
            "company_id",
            "previous_courier_id",
            "dispatched_by_id",
            "occurred_at",
        }
        if any(controlled.intersection(values) for values in vals_list):
            raise ValidationError(
                _(
                    "Delivery retry shipment, company, previous courier, actor, and timestamp are server-controlled."
                )
            )

        occurred_at = fields.Datetime.now()
        prepared = []
        for incoming in vals_list:
            values = dict(incoming)
            attempt = (
                self.env["parcel.delivery.attempt"]
                .browse(values.get("attempt_id"))
                .exists()
            )
            new_courier = (
                self.env["parcel.courier"].browse(values.get("new_courier_id")).exists()
            )
            if not attempt or not new_courier:
                raise ValidationError(
                    _("A valid delivery attempt and courier are required.")
                )
            shipment = attempt.shipment_id
            if shipment.state != "delivery_failed" or shipment.courier_id:
                raise UserError(_("Only a failed, unassigned shipment can be retried."))
            latest_attempt = self.env["parcel.delivery.attempt"].search(
                [("shipment_id", "=", shipment.id)],
                order="occurred_at desc, id desc",
                limit=1,
            )
            if latest_attempt != attempt or attempt.retry_ids:
                raise UserError(
                    _("Only the latest unresolved delivery attempt can be retried.")
                )
            if shipment.company_id != new_courier.company_id:
                raise ValidationError(
                    _("The courier and shipment must belong to the same company.")
                )
            reason = values.get("reason")
            if not reason or not reason.strip():
                raise ValidationError(_("A delivery retry reason is required."))
            values.update(
                {
                    "previous_courier_id": attempt.courier_id.id,
                    "dispatched_by_id": self.env.user.id,
                    "occurred_at": occurred_at,
                    "reason": reason.strip(),
                }
            )
            prepared.append(values)
        return super().create(prepared)

    def write(self, vals):
        raise AccessError(_("Delivery retries are append-only and cannot be changed."))

    def unlink(self):
        raise AccessError(_("Delivery retries are append-only and cannot be deleted."))
