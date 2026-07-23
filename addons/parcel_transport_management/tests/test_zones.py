from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import ParcelTestCase


@tagged("post_install", "-at_install")
class TestParcelZones(ParcelTestCase):
    def create_zone(self, name, prefix, company=None, sla_hours=24.0):
        company = company or self.company
        zone = (
            self.env["parcel.delivery.zone"]
            .with_company(company)
            .create(
                {
                    "name": name,
                    "company_id": company.id,
                    "default_sla_hours": sla_hours,
                }
            )
        )
        rule = (
            self.env["parcel.zone.postcode.rule"]
            .with_company(company)
            .create(
                {
                    "zone_id": zone.id,
                    "country_id": self.country.id,
                    "postcode_prefix": prefix,
                }
            )
        )
        return zone, rule

    def create_partner(self, name, postcode, company=None):
        company = company or self.company
        return self.env["res.partner"].create(
            {
                "name": name,
                "company_id": company.id,
                "street": "Structured test street 1",
                "city": "Test city",
                "zip": postcode,
                "country_id": self.country.id,
            }
        )

    def test_most_specific_postcode_prefix_wins(self):
        broad_zone, _broad_rule = self.create_zone("Broad Zone", "2")
        specific_zone, _specific_rule = self.create_zone("Specific Zone", "28013")

        shipment = self.create_shipment()

        self.assertNotEqual(shipment.origin_zone_id, broad_zone)
        self.assertEqual(shipment.origin_zone_id, specific_zone)
        self.assertEqual(shipment.destination_zone_id, self.zone)

    def test_archived_specific_zone_falls_back_without_changing_historic_shipments(
        self,
    ):
        broad_zone, _broad_rule = self.create_zone("Archive Fallback Zone", "280")
        specific_zone, _specific_rule = self.create_zone(
            "Archived Specific Zone", "28013"
        )
        historic_shipment = self.create_shipment()
        self.assertEqual(historic_shipment.origin_zone_id, specific_zone)

        specific_zone.active = False
        new_shipment = self.create_shipment()

        self.assertEqual(new_shipment.origin_zone_id, broad_zone)
        self.assertEqual(historic_shipment.origin_zone_id, specific_zone)

    def test_zone_company_is_allowed_on_create_and_immutable_afterward(self):
        zone_model = self.env["parcel.delivery.zone"].with_context(
            allowed_company_ids=self.company.ids
        )
        names = ["Allowed Batch Zone", "Forbidden Batch Zone"]

        with self.assertRaises(ValidationError):
            zone_model.create(
                [
                    {
                        "name": names[0],
                        "company_id": self.company.id,
                    },
                    {
                        "name": names[1],
                        "company_id": self.other_company.id,
                    },
                ]
            )

        self.assertFalse(
            self.env["parcel.delivery.zone"]
            .with_context(active_test=False)
            .search([("name", "in", names)])
        )
        with self.assertRaises(ValidationError):
            self.zone.write({"company_id": self.other_company.id})
        self.assertEqual(self.zone.company_id, self.company)

    def test_zone_sla_rejects_non_finite_values_on_create_and_write(self):
        original_value = self.zone.default_sla_hours

        for invalid_value in (float("inf"), float("-inf"), float("nan")):
            with self.subTest(value=invalid_value):
                zone_name = f"Invalid SLA {invalid_value!r} Zone"
                with self.assertRaises(ValidationError):
                    self.env["parcel.delivery.zone"].create(
                        {
                            "name": zone_name,
                            "company_id": self.company.id,
                            "default_sla_hours": invalid_value,
                        }
                    )
                self.assertFalse(
                    self.env["parcel.delivery.zone"]
                    .with_context(active_test=False)
                    .search([("name", "=", zone_name)])
                )

                with self.assertRaises(ValidationError):
                    self.zone.write({"default_sla_hours": invalid_value})
                self.assertEqual(self.zone.default_sla_hours, original_value)

    def test_normalized_postcode_prefix_is_unique_per_company_and_country(self):
        duplicate_zone = self.env["parcel.delivery.zone"].create(
            {
                "name": "Duplicate Prefix Zone",
                "company_id": self.company.id,
                "default_sla_hours": 24.0,
            }
        )

        with self.assertRaises(ValidationError):
            self.env["parcel.zone.postcode.rule"].create(
                {
                    "zone_id": duplicate_zone.id,
                    "country_id": self.country.id,
                    "postcode_prefix": " 28 ",
                }
            )

        other_zone, other_rule = self.create_zone(
            "Other Company Madrid Zone",
            "28",
            company=self.other_company,
        )
        self.assertEqual(other_rule.company_id, self.other_company)
        self.assertEqual(other_rule.zone_id, other_zone)

    def test_zone_resolution_is_isolated_by_shipment_company(self):
        other_zone, _other_rule = self.create_zone(
            "Other Company Specific Zone",
            "2801",
            company=self.other_company,
        )

        company_shipment = self.create_shipment()
        other_shipment = self.create_shipment(
            company=self.other_company,
            sender=self.other_sender,
            recipient=self.other_recipient,
        )

        self.assertEqual(company_shipment.origin_zone_id, self.zone)
        self.assertNotEqual(company_shipment.origin_zone_id, other_zone)
        self.assertEqual(other_shipment.origin_zone_id, other_zone)

    def test_address_and_zone_values_are_snapshotted_on_creation(self):
        shipment = self.create_shipment()
        expected_snapshot = (
            shipment.pickup_zip,
            shipment.pickup_country_id,
            shipment.delivery_zip,
            shipment.delivery_country_id,
            shipment.origin_zone_id,
            shipment.destination_zone_id,
            shipment.expected_delivery_at,
            shipment.original_expected_delivery_at,
        )

        self.sender.write({"zip": "99991"})
        self.recipient.write({"zip": "99992"})
        self.zone_rule.write({"postcode_prefix": "77"})

        self.assertEqual(
            (
                shipment.pickup_zip,
                shipment.pickup_country_id,
                shipment.delivery_zip,
                shipment.delivery_country_id,
                shipment.origin_zone_id,
                shipment.destination_zone_id,
                shipment.expected_delivery_at,
                shipment.original_expected_delivery_at,
            ),
            expected_snapshot,
        )
        self.assertEqual(shipment.pickup_zip, "28013")
        self.assertEqual(shipment.delivery_zip, "28080")
        self.assertEqual(shipment.pickup_country_id, self.country)
        self.assertEqual(shipment.delivery_country_id, self.country)

    def test_origin_and_destination_zones_are_resolved_independently(self):
        destination_zone, _destination_rule = self.create_zone(
            "Barcelona Test Zone", "08"
        )
        barcelona_recipient = self.create_partner("Barcelona Recipient", "08001")

        shipment = self.create_shipment(recipient=barcelona_recipient)

        self.assertEqual(shipment.origin_zone_id, self.zone)
        self.assertEqual(shipment.destination_zone_id, destination_zone)

    def test_destination_zone_default_sla_is_committed_on_assignment(self):
        destination_zone, _destination_rule = self.create_zone(
            "SLA Test Zone", "41", sla_hours=36.0
        )
        recipient = self.create_partner("Seville Recipient", "41001")
        shipment = self.create_shipment(recipient=recipient)
        self.assertFalse(shipment.expected_delivery_at)
        self.assertFalse(shipment.original_expected_delivery_at)
        before = fields.Datetime.now()

        shipment.action_assign(self.courier.id)

        after = fields.Datetime.now()
        self.assertEqual(shipment.destination_zone_id, destination_zone)
        self.assertGreaterEqual(
            shipment.expected_delivery_at,
            before + timedelta(hours=36),
        )
        self.assertLessEqual(
            shipment.expected_delivery_at,
            after + timedelta(hours=36),
        )
        self.assertEqual(
            shipment.original_expected_delivery_at,
            shipment.expected_delivery_at,
        )

    def test_assignment_coverage_warning_is_logged_in_chatter(self):
        _destination_zone, _destination_rule = self.create_zone(
            "Coverage Warning Zone", "41"
        )
        recipient = self.create_partner("Coverage Warning Recipient", "41001")
        shipment = self.create_shipment(recipient=recipient)
        note_subtype = self.env.ref("mail.mt_note")
        note_domain = [
            ("model", "=", "parcel.shipment"),
            ("res_id", "=", shipment.id),
            ("subtype_id", "=", note_subtype.id),
        ]
        before = self.env["mail.message"].search_count(note_domain)

        shipment.action_assign(self.courier.id)

        self.assertTrue(shipment.coverage_warning)
        self.assertGreater(
            self.env["mail.message"].search_count(note_domain),
            before,
        )

    def test_missing_zone_coverage_warns_without_blocking_creation(self):
        uncovered_sender = self.create_partner("Uncovered Sender", "99991")
        uncovered_recipient = self.create_partner("Uncovered Recipient", "99992")

        missing_origin = self.create_shipment(sender=uncovered_sender)
        missing_destination = self.create_shipment(recipient=uncovered_recipient)

        self.assertFalse(missing_origin.origin_zone_id)
        self.assertTrue(missing_origin.coverage_warning)
        self.assertEqual(len(missing_origin.package_ids), 1)
        self.assertFalse(missing_destination.destination_zone_id)
        self.assertTrue(missing_destination.coverage_warning)
        self.assertEqual(len(missing_destination.package_ids), 1)
