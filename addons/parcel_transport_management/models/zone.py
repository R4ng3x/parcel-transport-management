import math

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ParcelDeliveryZone(models.Model):
    _name = "parcel.delivery.zone"
    _description = "Parcel Delivery Zone"
    _order = "name, id"
    _check_company_auto = True

    name = fields.Char(required=True, index="trigram")
    active = fields.Boolean(default=True)
    code = fields.Char(index=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete="cascade",
    )
    default_sla_hours = fields.Float(
        string="Default SLA (Hours)",
        default=24.0,
        required=True,
    )
    postcode_rule_ids = fields.One2many(
        "parcel.zone.postcode.rule",
        "zone_id",
        string="Postcode Rules",
    )
    courier_ids = fields.Many2many(
        "parcel.courier",
        "parcel_courier_zone_rel",
        "zone_id",
        "courier_id",
        string="Couriers",
        check_company=True,
    )

    _default_sla_hours_positive = models.Constraint(
        "CHECK(default_sla_hours > 0)",
        "The default SLA must be strictly positive.",
    )

    def _validate_default_sla_hours(self, value):
        if not math.isfinite(value) or value <= 0:
            raise ValidationError(
                _(
                    "The default SLA must be a finite, strictly positive number of hours."
                )
            )

    @api.model_create_multi
    def create(self, vals_list):
        company_ids = {
            values.get("company_id") or self.env.company.id for values in vals_list
        }
        if not company_ids.issubset(self.env.companies.ids):
            raise ValidationError(
                _(
                    "You cannot create a delivery zone for a company outside your allowed companies."
                )
            )
        for values in vals_list:
            if "default_sla_hours" in values:
                self._validate_default_sla_hours(values["default_sla_hours"])
        return super().create(vals_list)

    def write(self, values):
        if "company_id" in values and any(
            zone.company_id.id != values["company_id"] for zone in self
        ):
            raise ValidationError(
                _("A delivery zone's company cannot be changed after creation.")
            )
        if "default_sla_hours" in values:
            self._validate_default_sla_hours(values["default_sla_hours"])
        return super().write(values)

    @api.constrains("default_sla_hours")
    def _check_default_sla_hours(self):
        for zone in self:
            self._validate_default_sla_hours(zone.default_sla_hours)


class ParcelZonePostcodeRule(models.Model):
    _name = "parcel.zone.postcode.rule"
    _description = "Parcel Zone Postcode Rule"
    _order = "company_id, country_id, postcode_prefix, id"
    _check_company_auto = True

    zone_id = fields.Many2one(
        "parcel.delivery.zone",
        required=True,
        check_company=True,
        index=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="zone_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )
    country_id = fields.Many2one(
        "res.country",
        required=True,
        index=True,
        ondelete="restrict",
    )
    postcode_prefix = fields.Char(required=True, index=True)

    _postcode_prefix_not_empty = models.Constraint(
        "CHECK(postcode_prefix <> '')",
        "The postcode prefix cannot be empty.",
    )
    _postcode_prefix_company_country_unique = models.Constraint(
        "UNIQUE(company_id, country_id, postcode_prefix)",
        "The postcode prefix must be unique per company and country.",
    )

    @api.model
    def _normalize_postcode(self, postcode):
        return "".join((postcode or "").split()).upper()

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        seen = set()
        for incoming in vals_list:
            values = dict(incoming)
            if "postcode_prefix" in values:
                values["postcode_prefix"] = self._normalize_postcode(
                    values["postcode_prefix"]
                )
            zone = self.env["parcel.delivery.zone"].browse(values.get("zone_id"))
            key = (
                zone.company_id.id,
                values.get("country_id"),
                values.get("postcode_prefix"),
            )
            if key in seen or self.search_count(
                [
                    ("company_id", "=", key[0]),
                    ("country_id", "=", key[1]),
                    ("postcode_prefix", "=", key[2]),
                ],
                limit=1,
            ):
                raise ValidationError(
                    _("The postcode prefix must be unique per company and country.")
                )
            seen.add(key)
            prepared.append(values)
        return super().create(prepared)

    def write(self, values):
        values = dict(values)
        if "postcode_prefix" in values:
            values["postcode_prefix"] = self._normalize_postcode(
                values["postcode_prefix"]
            )
        for rule in self:
            zone = self.env["parcel.delivery.zone"].browse(
                values.get("zone_id", rule.zone_id.id)
            )
            country_id = values.get("country_id", rule.country_id.id)
            prefix = values.get("postcode_prefix", rule.postcode_prefix)
            if self.search_count(
                [
                    ("id", "!=", rule.id),
                    ("company_id", "=", zone.company_id.id),
                    ("country_id", "=", country_id),
                    ("postcode_prefix", "=", prefix),
                ],
                limit=1,
            ):
                raise ValidationError(
                    _("The postcode prefix must be unique per company and country.")
                )
        return super().write(values)

    @api.model
    def _resolve(self, company, country, postcode):
        normalized_postcode = self._normalize_postcode(postcode)
        if not company or not country or not normalized_postcode:
            return self.env["parcel.delivery.zone"]

        company_id = company.id if hasattr(company, "id") else company
        country_id = country.id if hasattr(country, "id") else country
        possible_prefixes = [
            normalized_postcode[:length]
            for length in range(1, len(normalized_postcode) + 1)
        ]
        rules = self.search(
            [
                ("company_id", "=", company_id),
                ("country_id", "=", country_id),
                ("postcode_prefix", "in", possible_prefixes),
                ("zone_id.active", "=", True),
            ]
        )
        if not rules:
            return self.env["parcel.delivery.zone"]
        return max(rules, key=lambda rule: len(rule.postcode_prefix)).zone_id
