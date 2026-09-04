
import frappe
import requests
from frappe.utils import today, getdate, add_days, add_months, get_url, cint

CLICKNPAY_CREATE_URL = "https://backendservices.clicknpay.africa:2081/payme/orders"
CLICKNPAY_STATUS_URL = "https://backendservices.clicknpay.africa:2081/payme/orders/top-paid"

def get_public_id():
    # Configurable via site_config.json -> clicknpay_public_id
    # fallback to test key HQGVaTYJihldpvzsw (any site)
    return frappe.conf.get("clicknpay_public_id") or frappe.db.get_single_value("ClicknPay Settings", "public_unique_id") or "HQGVaTYJihldpvzsw"

def _find_or_create_invoice_for_subscription(subscription_name, plan_name=None, qty=1):
    """Return invoice_name, handling renewal creation like old Paynow script"""
    if not frappe.db.exists("Subscription", subscription_name):
        return None

    sub_doc = frappe.get_doc("Subscription", subscription_name)
    customer_name = sub_doc.party
    qty = cint(qty or 1) or 1

    if sub_doc.plans:
        if not plan_name:
            plan_name = sub_doc.plans[0].plan
        qty = sub_doc.plans[0].qty or qty

    # outstanding
    outstanding = frappe.get_all("Sales Invoice",
        filters={"subscription": subscription_name, "docstatus": 1, "outstanding_amount": [">", 0]},
        fields=["name"], order_by="creation desc", limit=1)
    if outstanding:
        return outstanding[0].name, subscription_name, plan_name, qty

    # create renewal invoice (12 months)
    if not plan_name or not frappe.db.exists("Subscription Plan", plan_name):
        frappe.throw(f"Subscription Plan {plan_name or ''} not found")

    plan_doc = frappe.get_doc("Subscription Plan", plan_name)
    new_start = today()
    if sub_doc.end_date and getdate(sub_doc.end_date) >= getdate(new_start):
        new_start = add_days(sub_doc.end_date, 1)
    new_end = add_days(add_months(new_start, 12), -1)

    inv_doc = frappe.get_doc({
        "doctype": "Sales Invoice",
        "customer": customer_name,
        "posting_date": today(),
        "due_date": add_days(today(), 3),
        "subscription": subscription_name,
        "from_date": new_start,
        "to_date": new_end,
        "items": [{"item_code": plan_doc.item, "qty": qty, "rate": plan_doc.cost or 0}]
    })
    inv_doc.insert(ignore_permissions=True)
    inv_doc.submit()
    frappe.db.set_value("Subscription", subscription_name, {
        "current_invoice_start": new_start,
        "current_invoice_end": new_end,
        "end_date": new_end
    })
    frappe.db.commit()
    return inv_doc.name, subscription_name, plan_name, qty


@frappe.whitelist(allow_guest=True)
def initiate_payment(reference=None, subscription=None, email=None, phone=None, plan_name=None, qty=1):
    """
    Main entry: replaces Paynow initiate.
    reference can be: Sales Invoice name, Subscription name, or email
    """
    reference = (reference or "").strip()
    subscription_arg = (subscription or "").strip()
    email = (email or "").strip()
    phone = (phone or "").strip()
    plan_name = (plan_name or "").strip()
    qty = cint(qty or 1) or 1
    if qty < 1:
        qty = 1

    invoice_name = None
    subscription_name = None
    site_url = get_url()
    final_plan = plan_name
    final_qty = qty

    # a) subscription arg -> find or create
    if subscription_arg:
        res = _find_or_create_invoice_for_subscription(subscription_arg, plan_name, qty)
        if res:
            invoice_name, subscription_name, final_plan, final_qty = res

    # b) reference as invoice
    if not invoice_name and reference:
        if frappe.db.exists("Sales Invoice", reference):
            invoice_name = reference
        elif frappe.db.exists("Subscription", reference):
            res = _find_or_create_invoice_for_subscription(reference, plan_name, qty)
            if res:
                invoice_name, subscription_name, final_plan, final_qty = res
                subscription_name = reference

    # c) fallback to subscription arg as reference
    if not invoice_name and subscription_arg and frappe.db.exists("Sales Invoice", subscription_arg):
        invoice_name = subscription_arg

    # d) email search (reference may actually be email)
    search_email = email or (reference if "@" in reference else "")
    if not invoice_name and search_email:
        # direct contact_email
        inv_list = frappe.get_all("Sales Invoice",
            filters={"contact_email": search_email, "docstatus": ["!=", 2]},
            fields=["name"], order_by="creation desc", limit=1)
        if inv_list:
            invoice_name = inv_list[0].name
        else:
            cust = frappe.db.get_value("Customer", {"email_id": search_email}, "name")
            if cust:
                inv_list = frappe.get_all("Sales Invoice",
                    filters={"customer": cust, "docstatus": ["!=", 2]},
                    fields=["name"], order_by="creation desc", limit=1)
                if inv_list:
                    invoice_name = inv_list[0].name

    if not invoice_name:
        frappe.throw(f"Invoice not found. Sub: {subscription_name or subscription_arg or '-'} Ref: {reference or '-'} Email: {search_email or '-'}")

    invoice = frappe.get_doc("Sales Invoice", invoice_name)

    # phone fallback
    if not phone:
        phone = frappe.db.get_value("Customer", invoice.customer, "mobile_no") or "263771234567"
    # ensure 263 format
    phone = phone.replace(" ", "").replace("-", "")

    # currency fallback
    currency = invoice.currency or "USD"

    # return url
    if subscription_name:
        return_url = f"{site_url}/subscriptions/{subscription_name}?status=success&invoice={invoice_name}"
    else:
        return_url = f"{site_url}/join-gw-keys?status=success&invoice={invoice_name}"

    # products
    products = []
    for idx, item in enumerate(invoice.items):
        products.append({
            "description": (item.description or item.item_name or item.item_code)[:100],
            "id": idx + 1,
            "price": float(item.rate or item.amount or 0),
            "productName": item.item_code,
            "quantity": int(item.qty or 1)
        })
    if not products:
        products = [{
            "description": f"GW Keys - {final_plan or 'Renewal'}",
            "id": 1,
            "price": float(invoice.grand_total or 0),
            "productName": final_plan or "GW-KEYS",
            "quantity": final_qty
        }]

    public_id = get_public_id()

    payload = {
        "channel": "AUTOMATED",
        "clientReference": invoice_name,
        "currency": currency,
        "customerCharged": True,
        "customerPhoneNumber": phone,
        "description": f"GW Keys - {final_plan or invoice.subscription or 'Renewal'} x{final_qty}",
        "multiplePayments": False,
        "orderYpe": "DYNAMIC",
        "productsList": products,
        "publicUniqueId": public_id,
        "returnUrl": return_url
    }

    try:
        resp = requests.post(CLICKNPAY_CREATE_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
        # ClicknPay returns 200 even on error, parse anyway
        try:
            j = resp.json()
        except:
            j = {"raw_text": resp.text, "status_code": resp.status_code}
    except Exception as e:
        frappe.log_error(title="ClicknPay HTTP Error", message=frappe.get_traceback() + "\nPayload: " + str(payload))
        frappe.throw(f"Unable to connect to ClicknPay: {str(e)}")

    pay_url = j.get("paymeURL") or j.get("paymeUrl") or j.get("paymentUrl") or j.get("url")

    if pay_url:
        return {
            "status": "success",
            "redirect_url": pay_url,
            "poll_url": f"{CLICKNPAY_STATUS_URL}/{invoice_name}",
            "invoice": invoice_name,
            "subscription": subscription_name,
            "raw": j
        }
    else:
        frappe.log_error(title="ClicknPay Rejected", message=f"Payload: {payload}\nResponse: {j}")
        return {
            "status": "error",
            "message": j.get("message") or j.get("error") or "ClicknPay rejected",
            "raw": j,
            "payload": payload
        }


@frappe.whitelist(allow_guest=True)
def check_status(reference):
    """GET https://backendservices.clicknpay.africa:2081/payme/orders/top-paid/{clientReference}"""
    if not reference:
        frappe.throw("reference required")
    try:
        r = requests.get(f"{CLICKNPAY_STATUS_URL}/{reference}", headers={"Content-Type": "application/json"}, timeout=15)
        try:
            return r.json()
        except:
            return {"raw": r.text, "status_code": r.status_code}
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}


@frappe.whitelist(allow_guest=True)
def clicknpay_callback():
    """
    Webhook / return handler.
    ClicknPay will redirect user to returnUrl which should point here or you can set returnUrl to this endpoint.
    Recommended returnUrl: /api/method/clicknpay_integration.api.clicknpay_callback?clientReference=INVOICE
    This will verify and create Payment Entry then redirect to final page.
    """
    data = frappe.form_dict
    client_ref = data.get("clientReference") or data.get("reference") or data.get("invoice") or data.get("client_reference")
    if not client_ref:
        frappe.throw("clientReference missing in callback")

    status_data = check_status(client_ref)
    # Normalize status
    status_val = (status_data.get("status") or "").upper()

    invoice_name = client_ref
    # ClicknPay sample returns {"status": "SUCCESS"/"FAILED", "clientReference": "..."}
    if status_val in ("SUCCESS", "PAID", "COMPLETED", "TOP_PAID", "PAID_SUCCESS"):
        try:
            inv = frappe.get_doc("Sales Invoice", invoice_name)
            if inv.docstatus == 1 and inv.outstanding_amount > 0:
                # Create Payment Entry
                pe = frappe.get_doc({
                    "doctype": "Payment Entry",
                    "payment_type": "Receive",
                    "party_type": "Customer",
                    "party": inv.customer,
                    "posting_date": today(),
                    "paid_amount": inv.grand_total,
                    "received_amount": inv.grand_total,
                    "reference_no": status_data.get("paymentGatewayReference") or status_data.get("correlator") or client_ref,
                    "reference_date": today(),
                    "mode_of_payment": "ClicknPay",
                    "references": [{
                        "reference_doctype": "Sales Invoice",
                        "reference_name": invoice_name,
                        "allocated_amount": inv.outstanding_amount
                    }]
                })
                pe.insert(ignore_permissions=True)
                pe.submit()
                frappe.db.commit()

                # Update subscription if linked
                if inv.subscription:
                    frappe.db.set_value("Subscription", inv.subscription, "status", "Active")
                    frappe.db.commit()

        except Exception as e:
            frappe.log_error(title="ClicknPay Callback PE Error", message=frappe.get_traceback() + f"\nStatusData: {status_data}")

    # Final redirect
    site_url = get_url()
    # preserve subscription if present
    sub = frappe.db.get_value("Sales Invoice", invoice_name, "subscription")
    if sub:
        final_url = f"{site_url}/subscriptions/{sub}?status=success&invoice={invoice_name}&gateway=clicknpay&gateway_status={status_val}"
    else:
        final_url = f"{site_url}/join-gw-keys?status=success&invoice={invoice_name}&gateway=clicknpay&gateway_status={status_val}"

    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = final_url
    return

# Legacy alias for old Paynow route compatibility
@frappe.whitelist(allow_guest=True)
def paynow_callback_alias():
    return clicknpay_callback()
