from odoo import api, models


class ParcelShipmentManifestReport(models.AbstractModel):
    _name = "report.parcel_transport_management.report_shipment_manifest"
    _description = "Parcel Shipment Manifest Report"

    @api.model
    def _get_report_values(self, docids, data=None):
        shipments = self.env["parcel.shipment"].browse(docids)
        shipments.check_access("read")
        return {
            "doc_ids": shipments.ids,
            "doc_model": "parcel.shipment",
            "docs": shipments,
        }


class ParcelPackageLabelReport(models.AbstractModel):
    _name = "report.parcel_transport_management.report_package_label"
    _description = "Parcel Package Label Report"

    @api.model
    def _get_report_values(self, docids, data=None):
        packages = self.env["parcel.package"].browse(docids)
        packages.check_access("read")
        return {
            "doc_ids": packages.ids,
            "doc_model": "parcel.package",
            "docs": packages,
        }
