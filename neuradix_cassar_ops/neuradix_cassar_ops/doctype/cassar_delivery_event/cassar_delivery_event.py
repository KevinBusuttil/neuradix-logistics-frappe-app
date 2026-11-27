import frappe
from frappe.model.document import Document


class CassarDeliveryEvent(Document):
	def validate(self) -> None:
		if not self.is_new():
			frappe.throw("Delivery evidence is immutable after creation.", frappe.ValidationError)

	def on_trash(self) -> None:
		frappe.throw("Delivery evidence cannot be deleted.", frappe.ValidationError)

