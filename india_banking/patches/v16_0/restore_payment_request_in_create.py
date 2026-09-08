import frappe


def execute():
	frappe.db.set_value("DocType", "Payment Request", "in_create", 1)
