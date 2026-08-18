from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import ParcelTestCase


@tagged("post_install", "-at_install")
class TestParcelCore(ParcelTestCase):
    def test_company_configuration_is_isolated_and_supplies_courier_defaults(self):
        self.assertEqual(self.company.parcel_max_package_weight, 30.0)
        self.assertEqual(self.company.parcel_max_package_weight_uom_id, self.kg_uom)
        self.assertEqual(self.other_company.parcel_max_package_weight, 70.0)
        self.assertEqual(
            self.other_company.parcel_max_package_weight_uom_id, self.lb_uom
        )

        courier = (
            self.env["parcel.courier"]
            .with_company(self.other_company)
            .create(
                {
                    "name": "Courier Using Company B Defaults",
                    "company_id": self.other_company.id,
                }
            )
        )

        self.assertEqual(courier.max_concurrent_shipments, 4)
        self.assertEqual(courier.max_concurrent_weight, 300.0)
        self.assertEqual(courier.max_weight_uom_id, self.lb_uom)

    def test_package_default_uom_uses_shipment_company(self):
        shipment = self.create_shipment(
            company=self.other_company,
            sender=self.other_sender,
            recipient=self.other_recipient,
            packages=[],
        )
        package_model = (
            self.env["parcel.package"]
            .with_context(
                allowed_company_ids=(self.company | self.other_company).ids,
            )
            .with_company(self.company)
        )

        package = package_model.create(
            {
                "shipment_id": shipment.id,
                "weight": 10.0,
            }
        )

        self.assertEqual(package.company_id, self.other_company)
        self.assertEqual(package.weight_uom_id, self.lb_uom)
        self.assertAlmostEqual(
            package.weight_kg,
            self.lb_uom._compute_quantity(10.0, self.kg_uom, round=False),
            places=6,
        )

    def test_package_default_get_uses_shipment_company(self):
        shipment = self.create_shipment(
            company=self.other_company,
            sender=self.other_sender,
            recipient=self.other_recipient,
            packages=[],
        )
        defaults = (
            self.env["parcel.package"]
            .with_context(
                allowed_company_ids=(self.company | self.other_company).ids,
                default_shipment_id=shipment.id,
            )
            .with_company(self.company)
            .default_get(["shipment_id", "weight_uom_id"])
        )

        self.assertEqual(defaults["shipment_id"], shipment.id)
        self.assertEqual(defaults["weight_uom_id"], self.lb_uom.id)

    def test_context_default_shipment_cannot_bypass_package_invariants(self):
        shipment = self.create_shipment()
        shipment.action_assign(self.courier.id)
        package_count = len(shipment.package_ids)

        with self.assertRaises(UserError):
            self.env["parcel.package"].with_context(
                default_shipment_id=shipment.id
            ).create({"weight": 1.0})

        self.assertEqual(len(shipment.package_ids), package_count)

    def test_settings_update_package_limit_and_uom_atomically(self):
        shipment = self.create_shipment(packages=[])
        self.create_package(shipment, weight=20.0, uom=self.kg_uom)
        self.company.write(
            {
                "parcel_max_package_weight": 70.0,
                "parcel_max_package_weight_uom_id": self.lb_uom.id,
            }
        )
        settings = self.env["res.config.settings"].create(
            {
                "company_id": self.company.id,
                "parcel_max_package_weight": 30.0,
                "parcel_max_package_weight_uom_id": self.kg_uom.id,
            }
        )

        settings.set_values()

        self.assertEqual(self.company.parcel_max_package_weight, 30.0)
        self.assertEqual(self.company.parcel_max_package_weight_uom_id, self.kg_uom)

    def test_courier_company_is_allowed_on_create_and_immutable_afterward(self):
        courier_model = self.env["parcel.courier"].with_context(
            allowed_company_ids=self.company.ids
        )
        names = ["Allowed Batch Courier", "Forbidden Batch Courier"]

        with self.assertRaises(ValidationError):
            courier_model.create(
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

        self.assertFalse(self.env["parcel.courier"].search([("name", "in", names)]))
        with self.assertRaises(ValidationError):
            self.courier.write({"company_id": self.other_company.id})
        self.assertEqual(self.courier.company_id, self.company)

    def test_company_weight_limits_reject_non_finite_values_on_create_and_write(self):
        fields_to_check = (
            "parcel_max_package_weight",
            "parcel_default_courier_max_weight",
        )
        invalid_values = (float("inf"), float("-inf"), float("nan"))

        for field_name in fields_to_check:
            original_value = self.company[field_name]
            for invalid_value in invalid_values:
                with self.subTest(field=field_name, value=invalid_value):
                    company_name = f"Invalid {field_name} {invalid_value!r} Company"
                    with self.assertRaises(ValidationError):
                        self.env["res.company"].create(
                            {
                                "name": company_name,
                                field_name: invalid_value,
                            }
                        )
                    self.assertFalse(
                        self.env["res.company"].search([("name", "=", company_name)])
                    )

                    with self.assertRaises(ValidationError):
                        self.company.write({field_name: invalid_value})
                    self.assertEqual(self.company[field_name], original_value)

    def test_courier_weight_limit_rejects_non_finite_values_on_create_and_write(self):
        original_value = self.courier.max_concurrent_weight

        for invalid_value in (float("inf"), float("-inf"), float("nan")):
            with self.subTest(value=invalid_value):
                courier_name = f"Invalid Weight {invalid_value!r} Courier"
                with self.assertRaises(ValidationError):
                    self.env["parcel.courier"].create(
                        {
                            "name": courier_name,
                            "company_id": self.company.id,
                            "max_concurrent_weight": invalid_value,
                        }
                    )
                self.assertFalse(
                    self.env["parcel.courier"].search([("name", "=", courier_name)])
                )

                with self.assertRaises(ValidationError):
                    self.courier.write({"max_concurrent_weight": invalid_value})
                self.assertEqual(
                    self.courier.max_concurrent_weight,
                    original_value,
                )

    def test_package_weight_rejects_non_finite_values_on_create_and_write(self):
        shipment = self.create_shipment()
        package = shipment.package_ids
        original_weight = package.weight

        for invalid_value in (float("inf"), float("-inf"), float("nan")):
            with self.subTest(value=invalid_value):
                with self.assertRaises(ValidationError):
                    self.env["parcel.package"].create(
                        {
                            "shipment_id": shipment.id,
                            "weight": invalid_value,
                            "weight_uom_id": self.kg_uom.id,
                        }
                    )

                with self.assertRaises(ValidationError):
                    package.write({"weight": invalid_value})
                self.assertEqual(package.weight, original_weight)

    def test_shipment_references_are_generated_and_unique(self):
        first = self.create_shipment()
        second = self.create_shipment()

        self.assertTrue(first.reference)
        self.assertTrue(second.reference)
        self.assertNotEqual(first.reference, "New")
        self.assertNotEqual(second.reference, "New")
        self.assertNotEqual(first.reference, second.reference)

    def test_ptm_tracking_codes_are_opaque_and_globally_unique(self):
        company_shipments = [self.create_shipment() for _index in range(6)]
        other_shipments = [
            self.create_shipment(
                company=self.other_company,
                sender=self.other_sender,
                recipient=self.other_recipient,
            )
            for _index in range(6)
        ]
        packages = self.env["parcel.package"].browse(
            [
                package.id
                for shipment in company_shipments + other_shipments
                for package in shipment.package_ids
            ]
        )
        tracking_codes = packages.mapped("tracking_code")

        self.assertEqual(len(tracking_codes), len(set(tracking_codes)))
        self.assertTrue(all(code.startswith("PTM") for code in tracking_codes))
        self.assertTrue(
            all(
                code != str(package.id)
                for code, package in zip(tracking_codes, packages, strict=True)
            )
        )

    def test_copy_generates_new_reference_and_package_tracking_codes(self):
        shipment = self.create_shipment(
            packages=[
                {"weight": 1.0, "weight_uom_id": self.kg_uom.id},
                {"weight": 2.0, "weight_uom_id": self.kg_uom.id},
            ]
        )

        copied = shipment.copy()

        self.assertNotEqual(copied, shipment)
        self.assertNotEqual(copied.reference, shipment.reference)
        self.assertEqual(len(copied.package_ids), len(shipment.package_ids))
        self.assertFalse(
            set(copied.package_ids.mapped("tracking_code"))
            & set(shipment.package_ids.mapped("tracking_code"))
        )
        self.assertEqual(copied.package_ids.mapped("shipment_id"), copied)

    def test_client_supplied_tracking_code_is_rejected(self):
        shipment = self.create_shipment()

        with self.assertRaises(ValidationError):
            self.create_package(
                shipment,
                tracking_code="PTM-CLIENT-SUPPLIED",
            )
        with self.assertRaises(ValidationError):
            shipment.package_ids.write({"tracking_code": "PTM-REPLACED"})

    def test_package_weight_must_remain_strictly_positive(self):
        shipment = self.create_shipment()

        with self.assertRaises(ValidationError):
            self.create_package(shipment, weight=0.0)
        with self.assertRaises(ValidationError):
            self.create_package(shipment, weight=-0.01)
        with self.assertRaises(ValidationError):
            shipment.package_ids.write({"weight": 0.0})

    def test_package_weight_kg_converts_kg_and_lb_without_rounding_loss(self):
        shipment = self.create_shipment(
            packages=[
                {"weight": 2.5, "weight_uom_id": self.kg_uom.id},
                {"weight": 2.5, "weight_uom_id": self.lb_uom.id},
            ]
        )
        kg_package = shipment.package_ids.filtered(
            lambda package: package.weight_uom_id == self.kg_uom
        )
        lb_package = shipment.package_ids.filtered(
            lambda package: package.weight_uom_id == self.lb_uom
        )
        expected_lb_in_kg = self.lb_uom._compute_quantity(2.5, self.kg_uom, round=False)

        self.assertAlmostEqual(kg_package.weight_kg, 2.5, places=6)
        self.assertAlmostEqual(lb_package.weight_kg, expected_lb_in_kg, places=6)
        self.assertAlmostEqual(
            shipment.total_weight_kg,
            2.5 + expected_lb_in_kg,
            places=6,
        )

    def test_company_maximum_package_weight_uses_configured_uom(self):
        self.company.write(
            {
                "parcel_max_package_weight": 10.0,
                "parcel_max_package_weight_uom_id": self.lb_uom.id,
            }
        )
        shipment = self.create_shipment(
            packages=[{"weight": 10.0, "weight_uom_id": self.lb_uom.id}]
        )

        self.assertEqual(len(shipment.package_ids), 1)
        with self.assertRaises(ValidationError):
            self.create_package(shipment, weight=10.01, uom=self.lb_uom)
        with self.assertRaises(ValidationError):
            self.create_package(shipment, weight=5.0, uom=self.kg_uom)

    def test_company_package_limit_change_rejects_invalidating_open_shipments(self):
        shipment = self.create_shipment(
            packages=[{"weight": 20.0, "weight_uom_id": self.kg_uom.id}]
        )
        original_limit = self.company.parcel_max_package_weight
        original_uom = self.company.parcel_max_package_weight_uom_id

        with self.assertRaises(ValidationError):
            self.company.write({"parcel_max_package_weight": 10.0})
        self.company.invalidate_recordset(
            [
                "parcel_max_package_weight",
                "parcel_max_package_weight_uom_id",
            ]
        )
        self.assertEqual(self.company.parcel_max_package_weight, original_limit)
        self.assertEqual(
            self.company.parcel_max_package_weight_uom_id,
            original_uom,
        )
        self.assertEqual(shipment.state, "draft")

        shipment.action_assign(self.courier.id)

        with self.assertRaises(ValidationError):
            self.company.write({"parcel_max_package_weight_uom_id": self.lb_uom.id})
        self.company.invalidate_recordset(
            [
                "parcel_max_package_weight",
                "parcel_max_package_weight_uom_id",
            ]
        )
        self.assertEqual(self.company.parcel_max_package_weight, original_limit)
        self.assertEqual(
            self.company.parcel_max_package_weight_uom_id,
            original_uom,
        )
        self.assertEqual(shipment.state, "assigned")

    def test_assignment_revalidates_package_weight_after_limit_change(self):
        shipment = self.create_shipment(
            packages=[{"weight": 20.0, "weight_uom_id": self.kg_uom.id}]
        )
        # Simulate a stale configuration state so the transition remains a
        # defense-in-depth boundary independent of the settings write guard.
        self.env.cr.execute(
            "UPDATE res_company SET parcel_max_package_weight = %s WHERE id = %s",
            [10.0, self.company.id],
        )
        self.company.invalidate_recordset(["parcel_max_package_weight"])

        with self.assertRaises(ValidationError):
            shipment.action_assign(self.courier.id)

        self.assertEqual(shipment.state, "draft")
        self.assertFalse(shipment.courier_id)

    def test_packages_are_mutable_only_before_operations_start(self):
        shipment = self.create_shipment()
        removable = self.create_package(shipment, weight=2.0)

        removable.write({"weight": 2.5})
        removable.unlink()
        shipment.package_ids.write({"weight": 1.5})

        shipment.action_assign(self.courier.id)
        shipment.action_record_pickup(shipment.package_ids.ids)
        shipment.action_start_transit()

        with self.assertRaises(UserError):
            shipment.package_ids.write({"weight": 2.0})
        with self.assertRaises(UserError):
            self.create_package(shipment, weight=1.0)
        with self.assertRaises(UserError):
            shipment.package_ids.unlink()
