from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import ParcelTestCase


@tagged("post_install", "-at_install")
class TestParcelReports(ParcelTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        internal_group = cls.env.ref("base.group_user")
        operator_group = cls.env.ref("parcel_transport_management.group_ptm_operator")
        cls.operator_user = (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "PTM Report Operator",
                    "login": "ptm-report-operator",
                    "email": "ptm-report-operator@example.test",
                    "company_id": cls.company.id,
                    "company_ids": [Command.set(cls.company.ids)],
                    "group_ids": [Command.set((internal_group | operator_group).ids)],
                }
            )
        )
        cls.manifest_action = cls.env.ref(
            "parcel_transport_management.action_report_shipment_manifest"
        )
        cls.label_action = cls.env.ref(
            "parcel_transport_management.action_report_package_label"
        )

    def _render_html(self, action, record_ids, user=None, company=None):
        renderer = self.env["ir.actions.report"]
        if user:
            renderer = renderer.with_user(user)
        if company:
            renderer = renderer.with_context(allowed_company_ids=company.ids)
        content, content_type = renderer._render_qweb_html(
            action.report_name,
            record_ids,
        )
        self.assertEqual(content_type, "html")
        return content.decode()

    def test_report_actions_render_operational_documents(self):
        shipment = self.create_shipment(
            packages=[
                {
                    "weight": 7.25,
                    "weight_uom_id": self.kg_uom.id,
                }
            ]
        )
        shipment.action_assign(self.courier.id)
        package = shipment.package_ids.ensure_one()

        self.assertEqual(self.manifest_action.model, "parcel.shipment")
        self.assertEqual(self.manifest_action.report_type, "qweb-pdf")
        self.assertEqual(
            self.manifest_action.binding_model_id.model,
            "parcel.shipment",
        )
        self.assertEqual(self.label_action.model, "parcel.package")
        self.assertEqual(self.label_action.report_type, "qweb-pdf")
        self.assertEqual(self.label_action.binding_model_id.model, "parcel.package")
        self.assertEqual(self.label_action.paperformat_id.page_width, 100)
        self.assertEqual(self.label_action.paperformat_id.page_height, 150)

        manifest_html = self._render_html(self.manifest_action, shipment.ids)
        self.assertIn(shipment.reference, manifest_html)
        self.assertIn(shipment.pickup_name, manifest_html)
        self.assertIn(shipment.delivery_name, manifest_html)
        self.assertIn(self.courier.name, manifest_html)
        self.assertIn(package.tracking_code, manifest_html)
        self.assertIn("7.25", manifest_html)

        label_html = self._render_html(self.label_action, package.ids)
        self.assertIn(shipment.reference, label_html)
        self.assertIn(package.tracking_code, label_html)
        self.assertIn("7.25", label_html)
        self.assertIn("data:image/png;base64,", label_html)
        self.assertIn("Code128 barcode", label_html)
        self.assertNotIn(shipment.pickup_name, label_html)
        self.assertNotIn(shipment.delivery_name, label_html)
        self.assertNotIn(shipment.delivery_street, label_html)

    def test_report_rendering_enforces_company_isolation(self):
        other_shipment = self.create_shipment(
            company=self.other_company,
            sender=self.other_sender,
            recipient=self.other_recipient,
        )
        other_package = other_shipment.package_ids.ensure_one()

        with self.assertRaises(AccessError):
            self._render_html(
                self.manifest_action,
                other_shipment.ids,
                user=self.operator_user,
                company=self.company,
            )
        with self.assertRaises(AccessError):
            self._render_html(
                self.label_action,
                other_package.ids,
                user=self.operator_user,
                company=self.company,
            )
