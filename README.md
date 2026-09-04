# ClicknPay Integration for Frappe / ERPNext

Generic, reusable Frappe app for **ClicknPay (openapi.africa)** payment gateway - built by **Cyteer Systems**.

Works on any Frappe site: `gwkeyslocksmiths.jh.frappe.cloud`, ERPNext, or any custom app. No hard-coded customer logic.

> API Docs: https://openapi.africa | Endpoints: `backendservices.clicknpay.africa:2081/payme/orders`

---

## Features

- **Create Order** – `POST /payme/orders` via `clicknpay_integration.api.initiate_payment`
- **Check Status** – `GET /orders/top-paid/{clientReference}`
- **Callback Handler** – Auto-creates `Payment Entry` and redirects to success page
- **Settings DocType** – Test / Live mode, Public Unique ID, URLs
- **site_config fallback** – `clicknpay_public_id` in `site_config.json`
- **Currency Support** – USD, ZWG, ZWL, AED (configurable)
- **Subscription Aware** – Finds outstanding Sales Invoice for Subscription

---

## Installation

### Frappe Cloud / Local Bench

```bash
# 1. Get app
bench get-app https://github.com/cyteersystems/clicknpay_integration.git

# 2. Install on site
bench --site [your.site.name] install-app clicknpay_integration

# 3. Migrate
bench --site [your.site.name] migrate

# 4. Clear cache
bench --site [your.site.name] clear-cache
```

Requires: `frappe >=15.0.0,<17.0.0`, Python >=3.10, `requests`

---

## Configuration

### Option A: Desk (Recommended)

Go to **Desk > ClicknPay Settings** (Single DocType)

- **Mode**: `Test` / `Live`
- **Test Public Unique ID**: `HQGVaTYJihldpvzsw` (default test ID from openapi.africa)
- **Live Public Unique ID**: Get from https://openapi.africa dashboard
- **Create Order URL**: `https://backendservices.clicknpay.africa:2081/payme/orders`
- **Status URL**: `https://backendservices.clicknpay.africa:2081/payme/orders/top-paid`

### Option B: site_config.json (for CI/CD)

```json
{
  "clicknpay_public_id": "YOUR_LIVE_PUBLIC_ID",
  "clicknpay_create_url": "https://backendservices.clicknpay.africa:2081/payme/orders",
  "clicknpay_status_url": "https://backendservices.clicknpay.africa:2081/payme/orders/top-paid"
}
```

---

## API Endpoints

All whitelisted with `allow_guest=True` for return URL.

### 1. Initiate Payment

```
GET /api/method/clicknpay_integration.api.initiate_payment
  ?reference=SINV-00001
  &subscription=SUB-00001
  &email=customer@example.com
  &phone=263771234567
  &plan_name=Gold Plan
  &qty=1
  &currency=USD
  &return_url=https://yoursite.com/payment-success
```

**Response (Success):**
```json
{
  "status": "success",
  "redirect_url": "https://payme.clicknpay.africa/...",
  "invoice": "SINV-00001",
  "raw": { "paymeURL": "...", "orderReference": "..." }
}
```

Payload sent to ClicknPay follows openapi.africa spec:
```json
{
  "channel": "AUTOMATED",
  "clientReference": "SINV-00001",
  "currency": "USD",
  "customerCharged": true,
  "customerPhoneNumber": "263771234567",
  "description": "Gold Plan x1",
  "multiplePayments": false,
  "orderYpe": "DYNAMIC",
  "productsList": [{"id":1,"productName":"Gold Plan","description":"...","price":50,"quantity":1}],
  "publicUniqueId": "HQGVaTYJihldpvzsw",
  "returnUrl": "https://yoursite.com/api/method/clicknpay_integration.api.clicknpay_callback?clientReference=SINV-00001"
}
```

### 2. Check Status

```
GET /api/method/clicknpay_integration.api.check_status?reference=SINV-00001
```

Proxies to `GET https://backendservices.clicknpay.africa:2081/payme/orders/top-paid/SINV-00001`

### 3. Callback (Called by ClicknPay)

```
GET /api/method/clicknpay_integration.api.clicknpay_callback?clientReference=SINV-00001
```

- Calls `check_status`
- If `SUCCESS/PAID/COMPLETED`, creates **Payment Entry** against Sales Invoice
- Redirects to `/payment-success?invoice=SINV-00001&status=SUCCESS`

---

## Frontend Integration

### Web Form (Registration)

See `v8.2` client script in repo. Key line:

```javascript
frappe.call({
  method: 'clicknpay_integration.api.initiate_payment',
  args: { reference: docName, email, phone, plan_name: plan, qty },
  callback: (r) => window.location.assign(r.message.redirect_url)
});
```

### Subscription Portal (`/subscriptions/[name]`)

Portal card calls same endpoint with `?invoice=SINV-xxx` support from email links.

Button file: `/files/clicknpay_button.png` (fallback to `/files/paynowbutton.png`)

---

## Email Notification

Create a **Notification** DocType:

- **Document Type**: `Sales Invoice`
- **Event**: `New`
- **Condition**: `doc.subscription and doc.outstanding_amount > 0`
- **Channel**: Email
- **Recipients**: `receiver_by_document_field = contact_email`
- **Message**: Use template in `/docs/email_template.html` – includes link:
  `{{ frappe.utils.get_url() }}/subscriptions/{{ doc.subscription }}?invoice={{ doc.name }}`

A helper Server Script `create_clicknpay_notification` is included to auto-create this.

---

## Replacing Paynow

If migrating from Paynow:

1. Disable Server Script `initiate_paynow_payment` / `click_and_pay_api`
2. Install this app
3. Update Web Form client scripts: `initiate_paynow_payment` → `clicknpay_integration.api.initiate_payment`
4. Replace Notification
5. Upload new button as `/files/clicknpay_button.png`

---

## Development

```
# Structure
clicknpay_integration/
  clicknpay_integration/
    hooks.py
    api.py
    clicknpay_integration/
      doctype/clicknpay_settings/clicknpay_settings.json
  pyproject.toml  # setuptools>=68, frappe >=15,<17
```

```toml
[tool.setuptools.packages.find]
where = ["."]
```

Build:

```bash
python -m build
```

---

## License

MIT – Cyteer Systems
Maintained for GW Keys Locksmiths & all Cyteer Systems Frappe apps.

## Support

- Issues: https://github.com/cyteersystems/clicknpay_integration/issues
- Email: support@cyteersystems.com
