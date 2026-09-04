import frappe
import requests
from frappe.utils import today, getdate, add_days, add_months, get_url, cint

CREATE_URL = "https://backendservices.clicknpay.africa:2081/payme/orders"
STATUS_URL = "https://backendservices.clicknpay.africa:2081/payme/orders/top-paid"

def get_settings():
    public_id = None
    try:
        if frappe.db.exists("DocType", "ClicknPay Settings"):
            doc = frappe.get_cached_doc("ClicknPay Settings")
            if doc.mode == "Live":
                public_id = doc.live_public_id
            else:
                public_id = doc.test_public_id
    except Exception:
        pass
    if not public_id:
        public_id = frappe.conf.get("clicknpay_public_id") or "HQGVaTYJihldpvzsw"
    return {
        "public_id": public_id,
        "create_url": frappe.conf.get("clicknpay_create_url") or CREATE_URL,
        "status_url": frappe.conf.get("clicknpay_status_url") or STATUS_URL,
    }

@frappe.whitelist(allow_guest=True)
def initiate_payment(reference=None, subscription=None, email=None, phone=None, plan_name=None, qty=1, description=None, return_url=None, currency=None):
    reference = (reference or "").strip()
    subscription_arg = (subscription or "").strip()
    email = (email or "").strip()
    phone = (phone or "").strip()
    qty = cint(qty or 1) or 1
    invoice_name = None
    subscription_name = None
    site_url = get_url()
    settings = get_settings()

    if subscription_arg and frappe.db.exists("Subscription", subscription_arg):
        outs = frappe.get_all("Sales Invoice", filters={"subscription": subscription_arg, "docstatus": 1, "outstanding_amount": [">", 0]}, limit=1, order_by="creation desc")
        if outs:
            invoice_name = outs[0].name
            subscription_name = subscription_arg

    if not invoice_name and reference:
        if frappe.db.exists("Sales Invoice", reference):
            invoice_name = reference
        elif frappe.db.exists("Subscription", reference):
            outs = frappe.get_all("Sales Invoice", filters={"subscription": reference, "docstatus": 1, "outstanding_amount": [">", 0]}, limit=1, order_by="creation desc")
            if outs:
                invoice_name = outs[0].name
                subscription_name = reference

    search_email = email or (reference if "@" in reference else "")
    if not invoice_name and search_email:
        inv = frappe.get_all("Sales Invoice", filters={"contact_email": search_email, "docstatus": ["!=", 2]}, limit=1, order_by="creation desc")
        if inv:
            invoice_name = inv[0].name

    if not invoice_name:
        frappe.throw(f"Invoice not found Ref: {reference}")

    inv_doc = frappe.get_doc("Sales Invoice", invoice_name)
    if not phone:
        phone = frappe.db.get_value("Customer", inv_doc.customer, "mobile_no") or "263771234567"
    curr = currency or inv_doc.currency or "USD"
    if not return_url:
        return_url = f"{site_url}/api/method/clicknpay_integration.api.clicknpay_callback?clientReference={invoice_name}"

    products = [{"description": (i.description or i.item_name)[:100], "id": idx+1, "price": float(i.rate or 0), "productName": i.item_code, "quantity": int(i.qty or 1)} for idx, i in enumerate(inv_doc.items)]
    if not products:
        products = [{"description": (description or "Payment")[:100], "id": 1, "price": float(inv_doc.grand_total), "productName": "ITEM", "quantity": qty}]

    payload = {
        "channel": "AUTOMATED",
        "clientReference": invoice_name,
        "currency": curr,
        "customerCharged": True,
        "customerPhoneNumber": phone.replace(" ", ""),
        "description": (description or f"Payment x{qty}")[:200],
        "multiplePayments": False,
        "orderYpe": "DYNAMIC",
        "productsList": products,
        "publicUniqueId": settings["public_id"],
        "returnUrl": return_url
    }

    try:
        resp = requests.post(settings["create_url"], json=payload, timeout=30)
        j = resp.json()
    except Exception as e:
        frappe.log_error(title="ClicknPay Create Error", message=f"{e}\nPayload: {payload}")
        return {"status": "error", "message": f"ClicknPay connection failed: {e}", "raw": str(e)}

    pay_url = j.get("paymeURL") or j.get("paymeUrl") or j.get("paymentUrl")
    if pay_url:
        return {"status": "success", "redirect_url": pay_url, "invoice": invoice_name, "raw": j}
    return {"status": "error", "message": j.get("message") or "No paymeURL returned", "raw": j}

@frappe.whitelist(allow_guest=True)
def check_status(reference):
    settings = get_settings()
    try:
        r = requests.get(f"{settings['status_url']}/{reference}", timeout=15)
        data = r.json()
        # Normalize - some APIs return list
        if isinstance(data, list) and data:
            return data[0]
        return data
    except Exception as e:
        frappe.log_error(title="ClicknPay Status Error", message=f"Ref {reference}: {e}")
        return {"status": "UNKNOWN", "error": str(e)}

@frappe.whitelist(allow_guest=True)
def clicknpay_callback():
    ref = frappe.form_dict.get("clientReference") or frappe.form_dict.get("reference") or frappe.form_dict.get("invoice")
    if not ref:
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = f"{get_url()}/payment-success?invoice=UNKNOWN&status=FAILED&reason=missing_reference"
        return

    status_data = {}
    status_val = "UNKNOWN"
    try:
        status_data = check_status(ref)
        status_val = (status_data.get("status") or status_data.get("paymentStatus") or "UNKNOWN").upper()
        # Handle list response where status inside
        if not status_val or status_val == "UNKNOWN":
            # Sometimes API returns transactionStatus
            status_val = (status_data.get("transactionStatus") or status_data.get("orderStatus") or "UNKNOWN").upper()
    except Exception as e:
        frappe.log_error(title="ClicknPay Callback Status Fetch Failed", message=f"Ref {ref}: {e}")
        status_val = "FAILED"

    # Success handling
    if status_val in ("SUCCESS", "PAID", "COMPLETED", "APPROVED"):
        try:
            if frappe.db.exists("Sales Invoice", ref):
                inv = frappe.get_doc("Sales Invoice", ref)
                if inv.outstanding_amount > 0:
                    # Check duplicate PE
                    existing = frappe.get_all("Payment Entry", filters={"reference_no": ref, "docstatus": ["<", 2]}, limit=1)
                    if not existing:
                        pe = frappe.get_doc({
                            "doctype": "Payment Entry",
                            "payment_type": "Receive",
                            "party_type": "Customer",
                            "party": inv.customer,
                            "posting_date": today(),
                            "paid_amount": inv.grand_total,
                            "received_amount": inv.grand_total,
                            "reference_no": ref,
                            "reference_date": today(),
                            "mode_of_payment": "ClicknPay",
                            "references": [{"reference_doctype": "Sales Invoice", "reference_name": ref, "allocated_amount": inv.outstanding_amount}]
                        })
                        pe.insert(ignore_permissions=True)
                        pe.submit()
                        frappe.db.commit()
        except Exception:
            frappe.log_error(title="ClicknPay Callback PE Error", message=frappe.get_traceback())
            # Don't fail the redirect - still show success but log
            status_val = "SUCCESS_WITH_WARNING"

    # Always redirect to success page, even on failure - never throw 500
    frappe.local.response["type"] = "redirect"
    reason = frappe.form_dict.get("reason") or status_data.get("message") or ""
    # Sanitize reason for URL
    import urllib.parse
    reason_qs = f"&reason={urllib.parse.quote_plus(str(reason)[:200])}" if reason else ""
    frappe.local.response["location"] = f"{get_url()}/payment-success?invoice={ref}&status={status_val}{reason_qs}"
