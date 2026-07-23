from odoo import http
from odoo.http import request


class ParcelPublicTracking(http.Controller):
    @staticmethod
    def _private_response(template_values):
        template_values["html_lang"] = request.env.lang.replace("_", "-")
        response = request.render(
            "parcel_transport_management.public_tracking_page",
            template_values,
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    def _tracking_response(self, tracking_code=None):
        page = {
            "csrf_token": request.csrf_token(),
            "tracking": None,
            "not_found": False,
        }
        if tracking_code is not None:
            package_model = request.env["parcel.package"]
            normalized_code = package_model._normalize_tracking_code(tracking_code)
            package = False
            if normalized_code:
                package = package_model.sudo().search(
                    [
                        ("tracking_code", "=", normalized_code),
                    ],
                    limit=1,
                )
            if package:
                page["tracking"] = package.get_public_tracking_data()
            else:
                page["not_found"] = True
        return self._private_response({"page": page})

    @http.route(
        "/parcel/track",
        type="http",
        auth="public",
        website=True,
        methods=["GET", "POST"],
        csrf=True,
        sitemap=False,
    )
    def tracking_form(self, tracking_code=None, **_kwargs):
        if request.httprequest.method == "POST":
            return self._tracking_response(tracking_code)
        return self._tracking_response()

    @http.route(
        "/parcel/track/<string:tracking_code>",
        type="http",
        auth="public",
        website=True,
        methods=["GET"],
        csrf=True,
        sitemap=False,
    )
    def tracking_code(self, tracking_code, **_kwargs):
        return self._tracking_response(tracking_code)
