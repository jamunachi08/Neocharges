"""Standard discount reasons (UNTDID 5189 allowance codes, the list ZATCA
validates AllowanceChargeReasonCode against). Seeded idempotently; users
can disable, rename the text, or add their own on top."""

import frappe

from neocharges.constants import REASON_DOCTYPE

STANDARD_REASONS = (
	("95", "Discount", "خصم"),
	("100", "Special rebate", "حسم خاص"),
	("104", "Standard", "خصم قياسي"),
	("103", "Temporary", "خصم مؤقت"),
	("102", "Fixed long term", "خصم ثابت طويل الأجل"),
	("105", "Yearly turnover", "خصم حجم المبيعات السنوي"),
	("64", "Special agreement", "اتفاقية خاصة"),
	("60", "Manufacturer's consumer discount", "خصم المصنع للمستهلك"),
	("66", "New outlet discount", "خصم منفذ بيع جديد"),
	("67", "Sample discount", "خصم العينات"),
	("68", "End-of-range discount", "خصم نهاية التشكيلة"),
	("65", "Production error discount", "خصم خطأ إنتاج"),
	("71", "Point of sales threshold allowance", "خصم حد نقطة البيع"),
	("70", "Incoterm discount", "خصم شروط التسليم"),
	("88", "Material surcharge/deduction", "خصم المواد"),
	("41", "Bonus for works ahead of schedule", "مكافأة إنجاز قبل الموعد"),
	("42", "Other bonus", "مكافأة أخرى"),
	("62", "Due to military status", "بسبب الوضع العسكري"),
	("63", "Due to work accident", "بسبب إصابة عمل"),
)

REASON_CODES = tuple(code for code, _en, _ar in STANDARD_REASONS)


def reason_name(code, text_en):
	return f"{code} - {text_en}"


def seed_reasons():
	if not frappe.db.exists("DocType", REASON_DOCTYPE):
		return
	for code, text_en, text_ar in STANDARD_REASONS:
		name = reason_name(code, text_en)
		if frappe.db.exists(REASON_DOCTYPE, name):
			continue
		frappe.get_doc(
			{
				"doctype": REASON_DOCTYPE,
				"reason_name": name,
				"reason_code": code,
				"reason_text_en": text_en,
				"reason_text_ar": text_ar,
			}
		).insert(ignore_permissions=True)
