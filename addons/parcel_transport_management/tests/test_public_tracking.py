import json
import re

from lxml import html
from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestPublicTracking(HttpCase):
    TRACKING_ROUTE = "/parcel/track"
    PUBLIC_DTO_KEYS = {
        "tracking_code",
        "current_status",
        "expected_delivery_at",
        "last_updated_at",
        "timeline",
    }
    TIMELINE_ITEM_KEYS = {"status", "occurred_at"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.spanish = (
            cls.env["res.lang"]
            .with_context(active_test=False)
            .search(
                [("code", "=", "es_ES")],
                limit=1,
            )
        )
        cls.spanish.active = True
        cls.website = cls.env["website"].get_current_website()
        cls.website.language_ids = [Command.link(cls.spanish.id)]
        cls.kg_uom = cls.env.ref("uom.product_uom_kgm")
        cls.company.write(
            {
                "parcel_max_package_weight": 30.0,
                "parcel_max_package_weight_uom_id": cls.kg_uom.id,
                "parcel_default_courier_max_shipments": 8,
                "parcel_default_courier_max_weight": 150.0,
                "parcel_default_courier_weight_uom_id": cls.kg_uom.id,
            }
        )

        cls.pii_markers = (
            "PII-SENDER-A-7N4Q",
            "PII-RECIPIENT-A-6H9M",
            "PII-ADDRESS-A-3K8W",
            "PII-COURIER-A-5R2X",
            "PII-PICKUP-NOTE-A-8D3J",
            "PII-DELIVERY-NOTE-A-4V7C",
            "PII-RECEIVED-BY-A-9F2L",
        )
        sender_a = cls.env["res.partner"].create(
            {
                "name": cls.pii_markers[0],
                "company_id": cls.company.id,
                "street": cls.pii_markers[2],
                "city": "Madrid",
                "zip": "28013",
                "email": "sender-a-private@example.test",
                "phone": "+34 600 111 222",
                "country_id": cls.env.ref("base.es").id,
            }
        )
        recipient_a = cls.env["res.partner"].create(
            {
                "name": cls.pii_markers[1],
                "company_id": cls.company.id,
                "street": "PII-RECIPIENT-ADDRESS-A-2C6P",
                "city": "Madrid",
                "zip": "28080",
                "email": "recipient-a-private@example.test",
                "phone": "+34 600 333 444",
                "country_id": cls.env.ref("base.es").id,
            }
        )
        cls.pii_markers += (
            "PII-RECIPIENT-ADDRESS-A-2C6P",
            "sender-a-private@example.test",
            "recipient-a-private@example.test",
            "+34 600 111 222",
            "+34 600 333 444",
        )
        cls.courier = cls.env["parcel.courier"].create(
            {
                "name": cls.pii_markers[3],
                "company_id": cls.company.id,
                "availability": "available",
                "max_concurrent_shipments": 8,
                "max_concurrent_weight": 150.0,
                "max_weight_uom_id": cls.kg_uom.id,
            }
        )
        shipment_a = cls.env["parcel.shipment"].create(
            {
                "company_id": cls.company.id,
                "sender_id": sender_a.id,
                "recipient_id": recipient_a.id,
                "package_ids": [
                    Command.create(
                        {
                            "weight": 1.0,
                            "weight_uom_id": cls.kg_uom.id,
                        }
                    )
                ],
            }
        )
        cls.package_a = shipment_a.package_ids
        shipment_a.action_assign(cls.courier.id)
        shipment_a.action_record_pickup(
            cls.package_a.ids,
            note=cls.pii_markers[4],
        )
        shipment_a.action_start_transit()
        shipment_a.action_record_delivery(
            cls.package_a.ids,
            recipient_name=cls.pii_markers[6],
            note=cls.pii_markers[5],
        )

        cls.package_b_marker = "PII-UNRELATED-PACKAGE-B-5T9Y"
        sender_b = cls.env["res.partner"].create(
            {
                "name": cls.package_b_marker,
                "company_id": cls.company.id,
            }
        )
        recipient_b = cls.env["res.partner"].create(
            {
                "name": "PII-UNRELATED-RECIPIENT-B-7U3N",
                "company_id": cls.company.id,
            }
        )
        shipment_b = cls.env["parcel.shipment"].create(
            {
                "company_id": cls.company.id,
                "sender_id": sender_b.id,
                "recipient_id": recipient_b.id,
                "package_ids": [
                    Command.create(
                        {
                            "weight": 2.0,
                            "weight_uom_id": cls.kg_uom.id,
                        }
                    )
                ],
            }
        )
        cls.package_b = shipment_b.package_ids

        cls.other_company = cls.env["res.company"].create(
            {"name": "Public Tracking Company B"}
        )
        cls.other_company.write(
            {
                "parcel_max_package_weight": 30.0,
                "parcel_max_package_weight_uom_id": cls.kg_uom.id,
                "parcel_default_courier_max_shipments": 8,
                "parcel_default_courier_max_weight": 150.0,
                "parcel_default_courier_weight_uom_id": cls.kg_uom.id,
            }
        )
        cls.cross_company_pii_markers = (
            "PII-CROSS-COMPANY-SENDER-2J6K",
            "PII-CROSS-COMPANY-RECIPIENT-4M8P",
            "cross-company-sender-private@example.test",
            "cross-company-recipient-private@example.test",
            "+34 600 555 666",
            "+34 600 777 888",
        )
        cls.pii_markers += cls.cross_company_pii_markers
        cross_company_sender = (
            cls.env["res.partner"]
            .with_company(cls.other_company)
            .create(
                {
                    "name": cls.cross_company_pii_markers[0],
                    "company_id": cls.other_company.id,
                    "email": cls.cross_company_pii_markers[2],
                    "phone": cls.cross_company_pii_markers[4],
                }
            )
        )
        cross_company_recipient = (
            cls.env["res.partner"]
            .with_company(cls.other_company)
            .create(
                {
                    "name": cls.cross_company_pii_markers[1],
                    "company_id": cls.other_company.id,
                    "email": cls.cross_company_pii_markers[3],
                    "phone": cls.cross_company_pii_markers[5],
                }
            )
        )
        cross_company_shipment = (
            cls.env["parcel.shipment"]
            .with_company(cls.other_company)
            .create(
                {
                    "company_id": cls.other_company.id,
                    "sender_id": cross_company_sender.id,
                    "recipient_id": cross_company_recipient.id,
                    "package_ids": [
                        Command.create(
                            {
                                "weight": 3.0,
                                "weight_uom_id": cls.kg_uom.id,
                            }
                        ),
                        Command.create(
                            {
                                "weight": 4.0,
                                "weight_uom_id": cls.kg_uom.id,
                            }
                        ),
                    ],
                }
            )
        )
        cls.cross_company_package = cross_company_shipment.package_ids[0]
        cls.cross_company_sibling = cross_company_shipment.package_ids[1]

        candidates = (
            "PTM-2222-2222-2222-2222",
            "PTM-3333-3333-3333-3333",
            "PTM-4444-4444-4444-4444",
        )
        cls.unknown_tracking_code = next(
            code
            for code in candidates
            if not cls.env["parcel.package"].search_count(
                [("tracking_code", "=", code)], limit=1
            )
        )

    def _open_form(self):
        response = self.url_open(self.TRACKING_ROUTE)
        self.assertEqual(response.status_code, 200)
        self._assert_private_indexing_headers(response)
        document = html.fromstring(response.content)
        forms = document.xpath("//form[.//input[@name='tracking_code']]")
        self.assertEqual(
            len(forms),
            1,
            "The public page must expose one semantic tracking form.",
        )
        form = forms[0]
        self.assertEqual((form.get("method") or "get").lower(), "post")
        return form

    def _post_tracking(self, tracking_code):
        form = self._open_form()
        payload = {"tracking_code": tracking_code}
        csrf_tokens = form.xpath(".//input[@name='csrf_token']/@value")
        if csrf_tokens:
            payload["csrf_token"] = csrf_tokens[0]
        response = self.url_open(
            form.get("action") or self.TRACKING_ROUTE,
            data=payload,
        )
        self.assertEqual(response.status_code, 200)
        self._assert_private_indexing_headers(response)
        return response

    def _assert_private_indexing_headers(self, response):
        cache_control = response.headers.get("Cache-Control", "").lower()
        robots = response.headers.get("X-Robots-Tag", "").lower()
        self.assertIn("no-store", cache_control)
        self.assertIn("noindex", robots)

    def _visible_main_text(self, response):
        document = html.fromstring(response.content)
        for hidden in document.xpath("//script|//style|//template"):
            hidden.drop_tree()
        roots = document.xpath("//main") or [document]
        return re.sub(r"\s+", " ", roots[0].text_content()).strip()

    def _assert_package_a_public_content(self, response, expected_status="delivered"):
        body = response.text
        self.assertIn(self.package_a.tracking_code, body)
        if expected_status:
            self.assertIn(expected_status, body.lower())
        for marker in self.pii_markers:
            self.assertNotIn(marker, body)
        self.assertNotIn(self.package_b.tracking_code, body)
        self.assertNotIn(self.package_b_marker, body)

    def test_tracking_form_and_valid_tracking_page(self):
        self._open_form()

        response = self.url_open(
            f"{self.TRACKING_ROUTE}/{self.package_a.tracking_code}"
        )

        self.assertEqual(response.status_code, 200)
        self._assert_private_indexing_headers(response)
        self._assert_package_a_public_content(response)

    def test_globally_unique_tracking_resolves_across_website_company(self):
        self.assertNotEqual(
            self.cross_company_package.company_id,
            self.website.company_id,
        )

        response = self._post_tracking(self.cross_company_package.tracking_code)

        body = response.text
        self.assertIn(self.cross_company_package.tracking_code, body)
        for marker in self.pii_markers:
            self.assertNotIn(marker, body)
        self.assertNotIn(self.cross_company_sibling.tracking_code, body)
        self.assertNotIn(self.package_a.tracking_code, body)
        self.assertNotIn(self.package_b.tracking_code, body)

    def test_tracking_page_uses_bcp47_request_language_and_stays_private(self):
        response = self.url_open(
            f"/es{self.TRACKING_ROUTE}/{self.package_a.tracking_code}"
        )

        self.assertEqual(response.status_code, 200)
        self._assert_private_indexing_headers(response)
        self._assert_package_a_public_content(response, expected_status=None)
        document = html.fromstring(response.content)
        self.assertEqual(document.get("lang"), "es-ES")
        status = document.xpath(
            "//p[contains(@class, 'o_ptm_tracking_current_status')]/strong"
        )
        self.assertEqual(len(status), 1)
        self.assertTrue(status[0].text_content().strip())

    def test_tracking_code_is_normalized_from_lowercase_without_hyphens(self):
        normalized_input = self.package_a.tracking_code.lower().replace("-", "")

        direct_response = self.url_open(f"{self.TRACKING_ROUTE}/{normalized_input}")
        form_response = self._post_tracking(normalized_input)

        for response in (direct_response, form_response):
            self.assertEqual(response.status_code, 200)
            self._assert_private_indexing_headers(response)
            self._assert_package_a_public_content(response)

    def test_invalid_and_unknown_tokens_have_same_safe_generic_response(self):
        malicious_token = (
            '"><img id="ptm-xss-sentinel" src="x" onerror="alert(1)">INVALID'
        )

        unknown_response = self._post_tracking(self.unknown_tracking_code)
        invalid_response = self._post_tracking(malicious_token)

        self.assertEqual(
            self._visible_main_text(unknown_response),
            self._visible_main_text(invalid_response),
        )
        for response in (unknown_response, invalid_response):
            self.assertNotIn(self.unknown_tracking_code, response.text)
            self.assertNotIn("ptm-xss-sentinel", response.text)
            document = html.fromstring(response.content)
            self.assertFalse(document.xpath("//*[@id='ptm-xss-sentinel']"))
            for marker in self.pii_markers:
                self.assertNotIn(marker, response.text)
            self.assertNotIn(self.package_b.tracking_code, response.text)

    def test_public_user_has_no_direct_package_access(self):
        public_user = self.env.ref("base.public_user")

        with self.assertRaises(AccessError):
            self.package_a.with_user(public_user).read(["tracking_code"])

    def test_public_tracking_dto_has_only_allowlisted_json_data_if_available(self):
        get_public_data = getattr(
            self.package_a,
            "get_public_tracking_data",
            None,
        )
        if get_public_data is None:
            return

        data = get_public_data()

        self.assertIsInstance(data, dict)
        self.assertEqual(set(data), self.PUBLIC_DTO_KEYS)
        self.assertEqual(data["tracking_code"], self.package_a.tracking_code)
        self.assertEqual(data["current_status"], "delivered")
        self.assertIsInstance(data["timeline"], list)
        self.assertTrue(data["timeline"])
        for item in data["timeline"]:
            self.assertIsInstance(item, dict)
            self.assertEqual(set(item), self.TIMELINE_ITEM_KEYS)
        serialized = json.dumps(data, sort_keys=True)
        for marker in self.pii_markers:
            self.assertNotIn(marker, serialized)
        self.assertNotIn(self.package_b.tracking_code, serialized)

    def test_failure_and_retry_are_publicly_generic_without_internal_reason(self):
        shipment = self.package_b.shipment_id
        courier_name = "PII-RETRY-COURIER-1H7N"
        failure_reason = "SECRET-FAILED-REASON-6Q4Z"
        retry_reason = "SECRET-RETRY-REASON-8M2P"
        courier = self.env["parcel.courier"].create(
            {
                "name": courier_name,
                "company_id": self.company.id,
                "availability": "available",
                "max_concurrent_shipments": 1,
                "max_concurrent_weight": 10.0,
                "max_weight_uom_id": self.kg_uom.id,
            }
        )
        shipment.action_assign(courier.id)
        shipment.action_record_pickup(shipment.package_ids.ids)
        shipment.action_start_transit()
        shipment.action_record_delivery_failure(failure_reason)
        shipment.action_retry_delivery(courier.id, retry_reason)

        data = self.package_b.get_public_tracking_data()

        self.assertEqual(set(data), self.PUBLIC_DTO_KEYS)
        self.assertEqual(data["current_status"], "in_transit")
        statuses = [item["status"] for item in data["timeline"]]
        failure_index = max(
            index
            for index, status in enumerate(statuses)
            if status == "delivery_failed"
        )
        self.assertTrue(
            any(status == "in_transit" for status in statuses[failure_index + 1 :])
        )
        for item in data["timeline"]:
            self.assertEqual(set(item), self.TIMELINE_ITEM_KEYS)
        serialized = json.dumps(data, sort_keys=True)
        for private_value in (
            failure_reason,
            retry_reason,
            courier_name,
            self.package_a.tracking_code,
        ):
            self.assertNotIn(private_value, serialized)

        response = self.url_open(
            f"{self.TRACKING_ROUTE}/{self.package_b.tracking_code}"
        )

        self.assertEqual(response.status_code, 200)
        self._assert_private_indexing_headers(response)
        visible_text = self._visible_main_text(response).lower()
        self.assertIn("delivery failed", visible_text)
        self.assertIn("in transit", visible_text)
        for private_value in (failure_reason, retry_reason, courier_name):
            self.assertNotIn(private_value, response.text)
        self.assertNotIn(self.package_a.tracking_code, response.text)
