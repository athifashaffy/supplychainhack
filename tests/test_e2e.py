"""
End-to-end Playwright tests for the TAN Supply Chain Provenance app.

Tests both the API directly and the browser UI across desktop + mobile viewports.
Run: pytest tests/test_e2e.py -v
"""

import pytest
import requests
from playwright.sync_api import Page, expect

BASE_URL = "http://localhost:3000"
API_URL = "http://localhost:8000"

# Known IDs from seed data (seeded before tests)
AURORA_ID = None   # set in fixture
TAMPER_ID = None
QTY_FRAUD_ID = None
CYCLE_ID = None


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def seed_data():
    """Seed demo + edge cases once per session. Skips if already populated."""
    global AURORA_ID, TAMPER_ID, QTY_FRAUD_ID, CYCLE_ID

    suppliers = requests.get(f"{API_URL}/api/suppliers").json()
    if len(suppliers) < 5:
        requests.post(f"{API_URL}/api/seed")

    atts = requests.get(f"{API_URL}/api/attestations").json()
    if not any("EC-01" in (a.get("product_name") or "") for a in atts):
        requests.post(f"{API_URL}/api/seed/edge-cases")

    atts = requests.get(f"{API_URL}/api/attestations").json()
    for a in atts:
        name = a.get("product_name", "")
        # Prefer sig_valid=True when picking canonical IDs
        if "Aurora-X" in name and a.get("sig_valid") and not AURORA_ID:
            AURORA_ID = a["id"]
        elif "EC-08" in name and "Tampered" in name and not TAMPER_ID:
            TAMPER_ID = a["id"]
        elif "EC-10:" in name and not QTY_FRAUD_ID:
            QTY_FRAUD_ID = a["id"]
        elif "EC-12c" in name and not CYCLE_ID:
            CYCLE_ID = a["id"]


MOBILE = {"width": 390, "height": 844}   # iPhone 14
TABLET = {"width": 768, "height": 1024}  # iPad


# ── API Tests ────────────────────────────────────────────────────────────────

class TestAPI:
    def test_health(self):
        r = requests.get(f"{API_URL}/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_suppliers_populated(self):
        r = requests.get(f"{API_URL}/api/suppliers")
        assert r.status_code == 200
        suppliers = r.json()
        assert len(suppliers) >= 5
        ids = [s["supplier_id"] for s in suppliers]
        assert "sup-001" in ids

    def test_attestations_populated(self):
        r = requests.get(f"{API_URL}/api/attestations")
        assert r.status_code == 200
        assert len(r.json()) >= 5

    def test_aurora_made_in_canada(self):
        r = requests.get(f"{API_URL}/api/product/{AURORA_ID}")
        assert r.status_code == 200
        d = r.json()
        assert d["designation"] == "Made in Canada"
        assert 80 < d["canadian_content_pct"] < 95
        assert d["last_transformation_in_canada"] is True
        assert d["dag_valid"] is True
        assert len(d["anomalies"]) == 0

    def test_aurora_chain_length(self):
        r = requests.get(f"{API_URL}/api/product/{AURORA_ID}")
        assert r.json()["chain_length"] == 5

    def test_aurora_signature_valid(self):
        r = requests.get(f"{API_URL}/api/verify/{AURORA_ID}")
        assert r.status_code == 200
        d = r.json()
        assert d["valid_signature"] is True
        assert d["known_signer"] is True
        assert d["anomalies"] == []

    def test_tampered_detected(self):
        if not TAMPER_ID:
            pytest.skip("Tampered attestation not found in DB")
        r = requests.get(f"{API_URL}/api/product/{TAMPER_ID}")
        d = r.json()
        types = [a["type"] for a in d["anomalies"]]
        assert "invalid_signature" in types

    def test_quantity_fraud_detected(self):
        if not QTY_FRAUD_ID:
            pytest.skip("Qty fraud attestation not found")
        r = requests.get(f"{API_URL}/api/product/{QTY_FRAUD_ID}")
        d = r.json()
        types = [a["type"] for a in d["anomalies"]]
        assert "quantity_fraud" in types

    def test_cycle_detected(self):
        if not CYCLE_ID:
            pytest.skip("Cycle attestation not found")
        r = requests.get(f"{API_URL}/api/product/{CYCLE_ID}")
        d = r.json()
        types = [a["type"] for a in d["anomalies"]]
        assert "cycle" in types

    def test_100pct_product_of_canada(self):
        atts = requests.get(f"{API_URL}/api/attestations").json()
        att_id = next((a["id"] for a in atts if "EC-01" in (a.get("product_name") or "")), None)
        if not att_id:
            pytest.skip("EC-01 not seeded")
        d = requests.get(f"{API_URL}/api/product/{att_id}").json()
        assert d["designation"] == "Product of Canada"
        assert d["canadian_content_pct"] == 100.0

    def test_0pct_not_qualified(self):
        atts = requests.get(f"{API_URL}/api/attestations").json()
        att_id = next((a["id"] for a in atts if "EC-02" in (a.get("product_name") or "")), None)
        if not att_id:
            pytest.skip("EC-02 not seeded")
        d = requests.get(f"{API_URL}/api/product/{att_id}").json()
        assert d["designation"] == "Not Qualified"
        assert d["canadian_content_pct"] == 0.0

    def test_exactly_98pct(self):
        atts = requests.get(f"{API_URL}/api/attestations").json()
        att_id = next((a["id"] for a in atts if "EC-03" in (a.get("product_name") or "")), None)
        if not att_id:
            pytest.skip("EC-03 not seeded")
        d = requests.get(f"{API_URL}/api/product/{att_id}").json()
        assert d["designation"] == "Product of Canada"
        assert d["canadian_content_pct"] == 98.0

    def test_exactly_51pct(self):
        atts = requests.get(f"{API_URL}/api/attestations").json()
        att_id = next((a["id"] for a in atts if "EC-05" in (a.get("product_name") or "")), None)
        if not att_id:
            pytest.skip("EC-05 not seeded")
        d = requests.get(f"{API_URL}/api/product/{att_id}").json()
        assert d["designation"] == "Made in Canada"
        assert d["canadian_content_pct"] == 51.0

    def test_below_51pct_not_qualified(self):
        atts = requests.get(f"{API_URL}/api/attestations").json()
        att_id = next((a["id"] for a in atts if "EC-06" in (a.get("product_name") or "")), None)
        if not att_id:
            pytest.skip("EC-06 not seeded")
        d = requests.get(f"{API_URL}/api/product/{att_id}").json()
        assert d["designation"] == "Not Qualified"
        assert d["canadian_content_pct"] == 50.9

    def test_last_transform_outside_canada(self):
        atts = requests.get(f"{API_URL}/api/attestations").json()
        att_id = next((a["id"] for a in atts if "EC-07:" in (a.get("product_name") or "")), None)
        if not att_id:
            pytest.skip("EC-07 not seeded")
        d = requests.get(f"{API_URL}/api/product/{att_id}").json()
        assert d["designation"] == "Not Qualified"
        assert d["last_transformation_in_canada"] is False

    def test_diamond_dag_no_double_count(self):
        atts = requests.get(f"{API_URL}/api/attestations").json()
        att_id = next((a["id"] for a in atts if "EC-13:" in (a.get("product_name") or "")), None)
        if not att_id:
            pytest.skip("EC-13 not seeded")
        d = requests.get(f"{API_URL}/api/product/{att_id}").json()
        # If D were double-counted, total would be ~$3600 and pct would differ
        assert d["total_cost_cad"] < 3000  # proves D counted once
        assert len(d["anomalies"]) == 0

    def test_zero_cost_no_crash(self):
        atts = requests.get(f"{API_URL}/api/attestations").json()
        att_id = next((a["id"] for a in atts if "EC-15" in (a.get("product_name") or "")), None)
        if not att_id:
            pytest.skip("EC-15 not seeded")
        d = requests.get(f"{API_URL}/api/product/{att_id}").json()
        assert d["canadian_content_pct"] == 0.0  # no crash, returns 0

    def test_missing_field_anomaly(self):
        atts = requests.get(f"{API_URL}/api/attestations").json()
        att_id = next((a["id"] for a in atts if "EC-14" in (a.get("product_name") or "")), None)
        if not att_id:
            pytest.skip("EC-14 not seeded")
        d = requests.get(f"{API_URL}/api/product/{att_id}").json()
        types = [a["type"] for a in d["anomalies"]]
        assert "missing_field" in types

    def test_unknown_signer_anomaly(self):
        atts = requests.get(f"{API_URL}/api/attestations").json()
        att_id = next((a["id"] for a in atts if "EC-09" in (a.get("product_name") or "")), None)
        if not att_id:
            pytest.skip("EC-09 not seeded")
        d = requests.get(f"{API_URL}/api/product/{att_id}").json()
        types = [a["type"] for a in d["anomalies"]]
        assert "unknown_signer" in types

    def test_missing_reference_anomaly(self):
        atts = requests.get(f"{API_URL}/api/attestations").json()
        att_id = next((a["id"] for a in atts if "EC-11" in (a.get("product_name") or "")), None)
        if not att_id:
            pytest.skip("EC-11 not seeded")
        d = requests.get(f"{API_URL}/api/product/{att_id}").json()
        types = [a["type"] for a in d["anomalies"]]
        assert "missing_reference" in types

    def test_qr_code_returns_png(self):
        r = requests.get(f"{API_URL}/api/qr/{AURORA_ID}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_404_on_missing_attestation(self):
        r = requests.get(f"{API_URL}/api/product/{'0' * 64}")
        assert r.status_code == 404

    def test_attest_invalid_signer_rejected(self):
        r = requests.post(f"{API_URL}/api/attest", json={
            "payload": {
                "supplier_id": "sup-999-nonexistent",
                "product_name": "Test",
                "timestamp": "2026-05-28T00:00:00Z",
                "location": {"country": "CA"},
                "inputs": [], "materials": [], "labour": None,
            },
            "signature": "00" * 64,
            "signer_id": "sup-999-nonexistent",
        })
        # Middleware (422) intercepts before the route handler (400) for unknown signers
        assert r.status_code in (400, 422)


# ── Browser UI Tests ────────────────────────────────────────────────────────

class TestDashboard:
    def test_dashboard_loads(self, page: Page):
        page.goto(f"{BASE_URL}/")
        expect(page.locator(".logo-text")).to_contain_text("Supply Chain")
        expect(page.locator("#stat-suppliers")).not_to_have_text("—", timeout=5000)

    def test_supplier_count_visible(self, page: Page):
        page.goto(f"{BASE_URL}/")
        page.wait_for_function("document.getElementById('stat-suppliers').textContent !== '—'")
        count = int(page.locator("#stat-suppliers").inner_text())
        assert count >= 5

    def test_attestation_table_renders(self, page: Page):
        page.goto(f"{BASE_URL}/")
        page.wait_for_selector("#att-table-wrap table", timeout=8000)
        rows = page.locator("#att-table-wrap tbody tr")
        assert rows.count() >= 5

    def test_nav_links_present(self, page: Page):
        page.goto(f"{BASE_URL}/")
        expect(page.locator("nav a[href='supplier.html']")).to_be_visible()
        expect(page.locator("nav a[href='purchaser.html']")).to_be_visible()


class TestPurchaser:
    def test_purchaser_loads(self, page: Page):
        page.goto(f"{BASE_URL}/purchaser.html")
        expect(page.locator(".page-title")).to_contain_text("Verify")

    def test_aurora_provenance_report(self, page: Page):
        page.goto(f"{BASE_URL}/purchaser.html?id={AURORA_ID}")
        # Wait for designation hero to appear
        page.wait_for_selector(".designation-hero", timeout=10000)
        hero = page.locator(".designation-hero")
        expect(hero).to_contain_text("Made in Canada")
        expect(hero).to_contain_text("84.5")

    def test_report_shows_chain(self, page: Page):
        page.goto(f"{BASE_URL}/purchaser.html?id={AURORA_ID}")
        page.wait_for_selector(".chain-node", timeout=10000)
        nodes = page.locator(".chain-node")
        assert nodes.count() == 5

    def test_anomaly_shown_for_tampered(self, page: Page):
        if not TAMPER_ID:
            pytest.skip("Tampered ID not found")
        page.goto(f"{BASE_URL}/purchaser.html?id={TAMPER_ID}")
        page.wait_for_selector(".anomaly-item", timeout=10000)
        anomaly = page.locator(".anomaly-item").first
        expect(anomaly).to_contain_text("invalid_signature")

    def test_qr_code_image_loads(self, page: Page):
        page.goto(f"{BASE_URL}/purchaser.html?id={AURORA_ID}")
        page.wait_for_selector(".qr-wrap img", timeout=10000)
        img = page.locator(".qr-wrap img")
        # Check src contains attestation ID
        src = img.get_attribute("src")
        assert AURORA_ID in src

    def test_lookup_by_input(self, page: Page):
        page.goto(f"{BASE_URL}/purchaser.html")
        page.fill("#lookup-id", AURORA_ID)
        page.click("button:has-text('Verify')")
        page.wait_for_selector(".designation-hero", timeout=10000)
        expect(page.locator(".designation-title")).to_contain_text("Made in Canada")

    def test_scan_qr_button_visible(self, page: Page):
        page.goto(f"{BASE_URL}/purchaser.html")
        expect(page.locator("button:has-text('Scan QR')")).to_be_visible()


class TestSupplier:
    def test_supplier_page_loads(self, page: Page):
        page.goto(f"{BASE_URL}/supplier.html")
        expect(page.locator(".page-title")).to_contain_text("Supplier")

    def test_registration_form_visible(self, page: Page):
        page.goto(f"{BASE_URL}/supplier.html")
        expect(page.locator("#reg-id")).to_be_visible()
        expect(page.locator("#reg-name")).to_be_visible()

    def test_attestation_form_visible(self, page: Page):
        page.goto(f"{BASE_URL}/supplier.html")
        expect(page.locator("#att-product-name")).to_be_visible()
        expect(page.locator("#att-private-key")).to_be_visible()


# ── Mobile Responsiveness Tests ─────────────────────────────────────────────

class TestMobileResponsive:
    def test_dashboard_mobile(self, page: Page):
        page.set_viewport_size(MOBILE)
        page.goto(f"{BASE_URL}/")
        # Nav links still visible
        expect(page.locator("nav")).to_be_visible()
        # Stats grid renders (2-col on mobile)
        expect(page.locator(".stat-grid")).to_be_visible()
        page.wait_for_selector("#stat-suppliers", timeout=5000)

    def test_purchaser_mobile(self, page: Page):
        page.set_viewport_size(MOBILE)
        page.goto(f"{BASE_URL}/purchaser.html?id={AURORA_ID}")
        page.wait_for_selector(".designation-hero", timeout=10000)
        # Designation hero fills screen width
        hero = page.locator(".designation-hero")
        box = hero.bounding_box()
        assert box["width"] > 300  # fills mobile screen

    def test_no_horizontal_overflow_mobile(self, page: Page):
        page.set_viewport_size(MOBILE)
        page.goto(f"{BASE_URL}/")
        # Check body doesn't overflow horizontally
        scroll_width = page.evaluate("document.body.scrollWidth")
        viewport_width = page.viewport_size["width"]
        assert scroll_width <= viewport_width + 5  # 5px tolerance

    def test_no_horizontal_overflow_purchaser_mobile(self, page: Page):
        page.set_viewport_size(MOBILE)
        page.goto(f"{BASE_URL}/purchaser.html?id={AURORA_ID}")
        page.wait_for_selector(".designation-hero", timeout=10000)
        scroll_width = page.evaluate("document.body.scrollWidth")
        viewport_width = page.viewport_size["width"]
        assert scroll_width <= viewport_width + 5

    def test_dashboard_tablet(self, page: Page):
        page.set_viewport_size(TABLET)
        page.goto(f"{BASE_URL}/")
        expect(page.locator(".stat-grid")).to_be_visible()
        expect(page.locator("nav")).to_be_visible()

    def test_supplier_form_mobile(self, page: Page):
        page.set_viewport_size(MOBILE)
        page.goto(f"{BASE_URL}/supplier.html")
        expect(page.locator("#reg-id")).to_be_visible()
        # Form fields stack vertically on mobile
        id_box = page.locator("#reg-id").bounding_box()
        name_box = page.locator("#reg-name").bounding_box()
        # On mobile, second field should be below first
        assert name_box["y"] > id_box["y"]


class TestHeader:
    """Verify TAN logo, title text, subtitle and nav on all 3 pages."""

    PAGES = [
        ("Dashboard",  f"{BASE_URL}/"),
        ("Supplier",   f"{BASE_URL}/supplier.html"),
        ("Purchaser",  f"{BASE_URL}/purchaser.html"),
    ]

    def _check_header(self, page, url):
        page.goto(url)
        # Logo image loads (not broken)
        img = page.locator(".logo-mark img")
        expect(img).to_be_visible()
        assert img.get_attribute("src") == "tan-logo.jpeg"
        # Title and subtitle
        expect(page.locator(".logo-text")).to_have_text("Supply Chain Provenance")
        expect(page.locator(".logo-sub")).to_have_text("Verified Canadian Content")
        # Nav links
        expect(page.locator("nav a[href='index.html']")).to_be_visible()
        expect(page.locator("nav a[href='supplier.html']")).to_be_visible()
        expect(page.locator("nav a[href='purchaser.html']")).to_be_visible()

    def test_header_dashboard(self, page):
        self._check_header(page, f"{BASE_URL}/")

    def test_header_supplier(self, page):
        self._check_header(page, f"{BASE_URL}/supplier.html")

    def test_header_purchaser(self, page):
        self._check_header(page, f"{BASE_URL}/purchaser.html")

    def test_logo_image_not_broken(self, page):
        page.goto(f"{BASE_URL}/")
        img = page.locator(".logo-mark img")
        # naturalWidth > 0 means image loaded successfully
        natural_width = page.evaluate(
            "document.querySelector('.logo-mark img').naturalWidth"
        )
        assert natural_width > 0, "TAN logo image failed to load"

    def test_header_mobile(self, page):
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{BASE_URL}/")
        expect(page.locator(".logo-mark img")).to_be_visible()
        expect(page.locator("nav")).to_be_visible()
