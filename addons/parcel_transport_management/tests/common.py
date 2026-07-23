from odoo import Command
from odoo.tests.common import TransactionCase


class ParcelTestCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create(
            {"name": "Parcel Test Company B"}
        )
        cls.kg_uom = cls.env.ref("uom.product_uom_kgm")
        cls.lb_uom = cls.env.ref("uom.product_uom_lb")
        cls.lb_uom.active = True

        cls.company.write(
            {
                "parcel_max_package_weight": 30.0,
                "parcel_max_package_weight_uom_id": cls.kg_uom.id,
                "parcel_default_courier_max_shipments": 8,
                "parcel_default_courier_max_weight": 150.0,
                "parcel_default_courier_weight_uom_id": cls.kg_uom.id,
            }
        )
        cls.other_company.write(
            {
                "parcel_max_package_weight": 70.0,
                "parcel_max_package_weight_uom_id": cls.lb_uom.id,
                "parcel_default_courier_max_shipments": 4,
                "parcel_default_courier_max_weight": 300.0,
                "parcel_default_courier_weight_uom_id": cls.lb_uom.id,
            }
        )

        cls.country = cls.env.ref("base.es")
        cls.sender = cls.env["res.partner"].create(
            {
                "name": "Parcel Test Sender",
                "company_id": cls.company.id,
                "street": "Calle Mayor 1",
                "street2": "Floor 2",
                "city": "Madrid",
                "zip": "28013",
                "country_id": cls.country.id,
            }
        )
        cls.recipient = cls.env["res.partner"].create(
            {
                "name": "Parcel Test Recipient",
                "company_id": cls.company.id,
                "street": "Calle de Serrano 10",
                "street2": "Door B",
                "city": "Madrid",
                "zip": "28080",
                "country_id": cls.country.id,
            }
        )
        cls.other_sender = cls.env["res.partner"].create(
            {
                "name": "Parcel Test Sender B",
                "company_id": cls.other_company.id,
                "street": "Calle de Alcala 20",
                "city": "Madrid",
                "zip": "28014",
                "country_id": cls.country.id,
            }
        )
        cls.other_recipient = cls.env["res.partner"].create(
            {
                "name": "Parcel Test Recipient B",
                "company_id": cls.other_company.id,
                "street": "Gran Via 30",
                "city": "Madrid",
                "zip": "28015",
                "country_id": cls.country.id,
            }
        )

        cls.zone = (
            cls.env["parcel.delivery.zone"]
            .with_company(cls.company)
            .create(
                {
                    "name": "Madrid Test Zone",
                    "company_id": cls.company.id,
                    "default_sla_hours": 24.0,
                }
            )
        )
        cls.zone_rule = (
            cls.env["parcel.zone.postcode.rule"]
            .with_company(cls.company)
            .create(
                {
                    "zone_id": cls.zone.id,
                    "country_id": cls.country.id,
                    "postcode_prefix": "28",
                }
            )
        )
        cls.courier = cls.create_courier()

    @classmethod
    def create_courier(cls, company=None, zone=None, **overrides):
        company = company or cls.company
        if zone is None:
            zone = (
                cls.zone if company == cls.company else cls.env["parcel.delivery.zone"]
            )
        values = {
            "name": "Parcel Test Courier",
            "company_id": company.id,
            "availability": "available",
            "max_concurrent_shipments": 8,
            "max_concurrent_weight": 150.0,
            "max_weight_uom_id": cls.kg_uom.id,
            "zone_ids": [Command.set(zone.ids)],
        }
        values.update(overrides)
        return cls.env["parcel.courier"].with_company(company).create(values)

    def create_shipment(
        self,
        company=None,
        sender=None,
        recipient=None,
        packages=None,
        **overrides,
    ):
        company = company or self.company
        sender = sender or self.sender
        recipient = recipient or self.recipient
        if packages is None:
            packages = [
                {
                    "weight": 1.0,
                    "weight_uom_id": self.kg_uom.id,
                }
            ]
        values = {
            "company_id": company.id,
            "sender_id": sender.id,
            "recipient_id": recipient.id,
            "package_ids": [Command.create(package) for package in packages],
        }
        values.update(overrides)
        return self.env["parcel.shipment"].with_company(company).create(values)

    def create_package(self, shipment, weight=1.0, uom=None, **overrides):
        values = {
            "shipment_id": shipment.id,
            "weight": weight,
            "weight_uom_id": (uom or self.kg_uom).id,
        }
        values.update(overrides)
        return (
            self.env["parcel.package"].with_company(shipment.company_id).create(values)
        )
