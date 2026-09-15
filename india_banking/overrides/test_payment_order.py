import json

import frappe
from frappe.tests.utils import FrappeTestCase

from india_banking.overrides.payment_order import get_party_summary
from india_banking.utils import get_party_field_name


class TestGetPartyFieldName(FrappeTestCase):
	def test_maps_known_party_types_to_their_name_field(self):
		self.assertEqual(get_party_field_name("Supplier"), "supplier_name")
		self.assertEqual(get_party_field_name("Customer"), "customer_name")
		self.assertEqual(get_party_field_name("Employee"), "employee_name")

	def test_falls_back_to_name_for_unknown_party_types(self):
		self.assertEqual(get_party_field_name("Shareholder"), "name")
		self.assertEqual(get_party_field_name("Something Else"), "name")


class TestPaymentOrderPartyName(FrappeTestCase):
	"""
	Covers the party name resolution added for the Payment Order Summary
	table: get_party_summary (client-side row builder) and
	CustomPaymentOrder.validate_summary (server-side backfill on save).
	"""

	company = "_Test Company"
	supplier_name = "IB Test Supplier"
	bank_name = "IB Test Bank"
	company_bank_account_name = "IB Test Company Account"
	supplier_bank_account_name = "IB Test Supplier Account"

	def setUp(self):
		self._created_docs = []
		self._create_base_fixtures()

	def tearDown(self):
		for doctype, name in reversed(self._created_docs):
			if not frappe.db.exists(doctype, name):
				continue
			doc = frappe.get_doc(doctype, name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def _track(self, doctype, name):
		self._created_docs.append((doctype, name))
		return name

	def _create_base_fixtures(self):
		if not frappe.db.exists("Supplier", self.supplier_name):
			supplier = frappe.get_doc(
				{
					"doctype": "Supplier",
					"supplier_name": self.supplier_name,
					"supplier_group": frappe.db.get_value("Supplier Group", {}, "name"),
					"supplier_type": "Company",
				}
			)
			supplier.insert()
			self._track("Supplier", supplier.name)

		if not frappe.db.exists("Bank", self.bank_name):
			bank = frappe.get_doc({"doctype": "Bank", "bank_name": self.bank_name})
			bank.insert()
			self._track("Bank", bank.name)

		if not frappe.db.exists(
			"Bank Account", {"account_name": self.company_bank_account_name}
		):
			company_account = frappe.get_doc(
				{
					"doctype": "Bank Account",
					"account_name": self.company_bank_account_name,
					"bank": self.bank_name,
					"company": self.company,
					"is_company_account": 1,
					"is_default": 1,
					"account": "_Test Bank - _TC",
					"branch_code": "HDFC0001234",
					"email": "company-account@example.com",
					"mobile_number": "9999999999",
				}
			)
			company_account.insert()
			self._track("Bank Account", company_account.name)
			self.company_bank_account = company_account.name
		else:
			self.company_bank_account = frappe.db.get_value(
				"Bank Account", {"account_name": self.company_bank_account_name}, "name"
			)

		if not frappe.db.exists(
			"Bank Account", {"account_name": self.supplier_bank_account_name}
		):
			supplier_account = frappe.get_doc(
				{
					"doctype": "Bank Account",
					"account_name": self.supplier_bank_account_name,
					"bank": self.bank_name,
					"party_type": "Supplier",
					"party": self.supplier_name,
					"is_default": 1,
					"branch_code": "HDFC0001234",
					"email": "supplier-account@example.com",
				}
			)
			supplier_account.insert()
			self._track("Bank Account", supplier_account.name)
			self.supplier_bank_account = supplier_account.name
		else:
			self.supplier_bank_account = frappe.db.get_value(
				"Bank Account",
				{"account_name": self.supplier_bank_account_name},
				"name",
			)

	def _create_purchase_invoice(self, rate=1000):
		pi = frappe.get_doc(
			{
				"doctype": "Purchase Invoice",
				"supplier": self.supplier_name,
				"company": self.company,
				"posting_date": "2050-01-15",
				"due_date": "2050-01-15",
				"set_posting_time": 1,
				"items": [
					{
						"item_code": "SKU001",
						"item_name": "SKU001",
						"qty": 1,
						"rate": rate,
						"expense_account": "Administrative Expenses - _TC",
						"cost_center": "_Test Cost Center - _TC",
					}
				],
				"credit_to": "Creditors - _TC",
			}
		)
		pi.insert()
		pi.submit()
		self._track("Purchase Invoice", pi.name)
		return pi

	def _create_payment_order(self, amount=1000, party_name=None):
		summary_row = {
			"party_type": "Supplier",
			"party": self.supplier_name,
			"amount": amount,
			"bank_account": self.supplier_bank_account,
			"mode_of_transfer": "NEFT",
		}
		if party_name is not None:
			summary_row["party_name"] = party_name

		pi = self._create_purchase_invoice(rate=amount)

		po = frappe.get_doc(
			{
				"doctype": "Payment Order",
				"company": self.company,
				"company_bank_account": self.company_bank_account,
				"account": "_Test Bank - _TC",
				"payment_order_type": "Payment Request",
				"posting_date": "2050-01-15",
				"references": [
					{
						"reference_doctype": "Purchase Invoice",
						"reference_name": pi.name,
						"amount": amount,
						"party_type": "Supplier",
						"party": self.supplier_name,
						"is_adhoc": 1,
						"bank_account": self.supplier_bank_account,
					}
				],
				"summary": [summary_row],
			}
		)
		po.insert()
		self._track("Payment Order", po.name)
		return po

	def test_get_party_summary_resolves_party_name(self):
		references = [
			{
				"party_type": "Supplier",
				"party": self.supplier_name,
				"bank_account": self.supplier_bank_account,
				"account": "",
				"cost_center": "",
				"project": "",
				"tax_withholding_category": "",
				"reference_doctype": "Purchase Invoice",
				"reference_name": "PI-0001",
				"payment_entry": "",
				"journal_entry_account": "",
				"amount": 1000,
			}
		]

		result = get_party_summary(
			references=json.dumps(references),
			company_bank_account=self.company_bank_account,
		)

		self.assertEqual(len(result), 1)
		self.assertEqual(result[0]["party_name"], self.supplier_name)

	def test_validate_summary_backfills_empty_party_name(self):
		po = self._create_payment_order(amount=1000, party_name="")

		self.assertEqual(po.summary[0].party_name, self.supplier_name)

	def test_validate_summary_preserves_existing_party_name(self):
		po = self._create_payment_order(amount=1000, party_name="Custom Display Name")

		self.assertEqual(po.summary[0].party_name, "Custom Display Name")
