# Copyright (c) 2024, Aerele Technologies Private Limited and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase


class TestBackfillPartyName(FrappeTestCase):
	"""
	Covers BankConnector.backfill_party_name, which self-heals Payment Order
	Summary rows whose party_name was never resolved (e.g. Payment Orders
	submitted before that fix shipped) when a payment payload is built.
	"""

	company = "_Test Company"
	bank_name = "IB Test Bank"
	company_bank_account_name = "IB Test Company Account"
	supplier_names = ["IB Backfill Supplier 1", "IB Backfill Supplier 2"]

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

		self.supplier_bank_accounts = {}
		for supplier_name in self.supplier_names:
			if not frappe.db.exists("Supplier", supplier_name):
				supplier = frappe.get_doc(
					{
						"doctype": "Supplier",
						"supplier_name": supplier_name,
						"supplier_group": frappe.db.get_value(
							"Supplier Group", {}, "name"
						),
						"supplier_type": "Company",
					}
				)
				supplier.insert()
				self._track("Supplier", supplier.name)

			account_name = f"{supplier_name} Account"
			if not frappe.db.exists("Bank Account", {"account_name": account_name}):
				supplier_account = frappe.get_doc(
					{
						"doctype": "Bank Account",
						"account_name": account_name,
						"bank": self.bank_name,
						"party_type": "Supplier",
						"party": supplier_name,
						"is_default": 1,
						"branch_code": "HDFC0001234",
						"email": f"{frappe.scrub(supplier_name)}@example.com",
					}
				)
				supplier_account.insert()
				self._track("Bank Account", supplier_account.name)
				self.supplier_bank_accounts[supplier_name] = supplier_account.name
			else:
				self.supplier_bank_accounts[supplier_name] = frappe.db.get_value(
					"Bank Account", {"account_name": account_name}, "name"
				)

	def _create_purchase_invoice(self, supplier_name, rate=1000):
		pi = frappe.get_doc(
			{
				"doctype": "Purchase Invoice",
				"supplier": supplier_name,
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

	def _create_payment_order(self):
		summary = []
		references = []
		for supplier_name in self.supplier_names:
			bank_account = self.supplier_bank_accounts[supplier_name]
			pi = self._create_purchase_invoice(supplier_name, rate=1000)
			summary.append(
				{
					"party_type": "Supplier",
					"party": supplier_name,
					"amount": 1000,
					"bank_account": bank_account,
					"mode_of_transfer": "NEFT",
				}
			)
			references.append(
				{
					"reference_doctype": "Purchase Invoice",
					"reference_name": pi.name,
					"amount": 1000,
					"party_type": "Supplier",
					"party": supplier_name,
					"is_adhoc": 1,
					"bank_account": bank_account,
				}
			)

		po = frappe.get_doc(
			{
				"doctype": "Payment Order",
				"company": self.company,
				"company_bank_account": self.company_bank_account,
				"account": "_Test Bank - _TC",
				"payment_order_type": "Payment Request",
				"posting_date": "2050-01-15",
				"references": references,
				"summary": summary,
			}
		)
		po.insert()
		self._track("Payment Order", po.name)
		return po

	def _clear_party_names(self, po):
		for row in po.summary:
			frappe.db.set_value(
				"Payment Order Summary",
				row.name,
				"party_name",
				"",
				update_modified=False,
			)
		po.reload()

	def test_backfill_resolves_and_persists_missing_party_names(self):
		po = self._create_payment_order()
		self._clear_party_names(po)
		for row in po.summary:
			self.assertFalse(row.party_name)

		frappe.new_doc("Bank Connector").backfill_party_name(po)

		for row, supplier_name in zip(po.summary, self.supplier_names):
			self.assertEqual(row.party_name, supplier_name)
			self.assertEqual(
				frappe.db.get_value("Payment Order Summary", row.name, "party_name"),
				supplier_name,
			)

	def test_backfill_skips_rows_that_already_have_a_party_name(self):
		po = self._create_payment_order()
		self._clear_party_names(po)
		po.summary[1].party_name = "Custom Display Name"

		frappe.new_doc("Bank Connector").backfill_party_name(po)

		self.assertEqual(po.summary[0].party_name, self.supplier_names[0])
		self.assertEqual(po.summary[1].party_name, "Custom Display Name")

	def test_backfill_batches_lookups_per_party_type(self):
		po = self._create_payment_order()
		self._clear_party_names(po)
		bank_connector = frappe.new_doc("Bank Connector")

		with patch(
			"india_banking.india_banking.doctype.bank_connector.bank_connector.frappe.get_all",
			wraps=frappe.get_all,
		) as get_all_spy:
			bank_connector.backfill_party_name(po)

		supplier_calls = [
			call
			for call in get_all_spy.call_args_list
			if call.args[:1] == ("Supplier",)
		]
		self.assertEqual(get_all_spy.call_count, len(supplier_calls))
		self.assertEqual(len(supplier_calls), 1)
