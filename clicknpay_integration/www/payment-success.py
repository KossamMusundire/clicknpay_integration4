import frappe

def get_context(context):
    context.no_cache = 1
    context.no_breadcrumbs = 1
    # Get params safely - never throw
    try:
        invoice = frappe.form_dict.get("invoice") or "UNKNOWN"
        status = (frappe.form_dict.get("status") or "UNKNOWN").upper()
        reason = frappe.form_dict.get("reason") or ""
        
        context.invoice = invoice
        context.status_raw = status
        context.reason = reason
        
        # Normalize status
        if status in ("SUCCESS", "PAID", "COMPLETED", "APPROVED", "SUCCESS_WITH_WARNING"):
            context.is_success = True
            context.status_label = "PAID"
            context.status_class = "success"
        elif status in ("FAILED", "DECLINED", "CANCELLED", "CANCELED", "ERROR", "UNKNOWN"):
            context.is_success = False
            context.status_label = status if status != "UNKNOWN" else "FAILED"
            context.status_class = "failed"
        else:
            context.is_success = False
            context.status_label = status
            context.status_class = "failed"

        # Try to get invoice details - fail safe
        context.invoice_doc = None
        context.customer_name = ""
        context.amount = ""
        try:
            if frappe.db.exists("Sales Invoice", invoice):
                inv = frappe.get_doc("Sales Invoice", invoice)
                context.invoice_doc = inv
                context.customer_name = inv.customer_name
                context.amount = f"{inv.currency} {inv.grand_total}"
                context.subscription = inv.subscription
            else:
                context.customer_name = ""
        except Exception:
            pass

    except Exception as e:
        frappe.log_error(title="payment-success get_context error", message=str(e))
        context.invoice = "UNKNOWN"
        context.is_success = False
        context.status_label = "ERROR"
        context.status_class = "failed"
        context.reason = "Error loading page"

    context.title = "Payment Status"
    return context
