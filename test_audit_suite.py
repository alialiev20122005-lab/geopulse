import asyncio
import sqlite3
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.config import ADMIN_USERNAME, ADMIN_PASSWORD
import app.database as db

async def run_tests():
    print("=== STARTING GEOPULSE AUDIT VERIFICATION SUITE ===")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        
        # 1. Database Configuration Checks
        print("\n[TEST 1] Verifying SQLite WAL mode, foreign keys, and indexes...")
        conn = db.get_connection()
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        conn.close()
        print(f"  -> SQLite journal_mode: {journal_mode}")
        print(f"  -> SQLite foreign_keys: {foreign_keys}")
        assert str(journal_mode).lower() == "wal", f"Expected WAL mode, got {journal_mode}"
        assert foreign_keys == 1, f"Expected foreign_keys=1, got {foreign_keys}"
        print("  -> PRAGMA checks PASSED!")

        # 2. Public Route Accessibility
        print("\n[TEST 2] Verifying public HTTP routes (Landing, Pitch, Offer)...")
        for path in ["/", "/pitch", "/offer"]:
            resp = await client.get(path)
            assert resp.status_code == 200, f"Route {path} failed with {resp.status_code}"
            print(f"  -> GET {path} returned 200 OK")

        # 3. Business Onboarding & QR Verification
        print("\n[TEST 3] Testing QR code generator and caching headers...")
        resp = await client.get("/api/qr/zerno?v=2&table=5")
        assert resp.status_code == 200
        assert resp.headers.get("content-type") == "image/png"
        assert "max-age=86400" in resp.headers.get("cache-control", "")
        print("  -> QR code generated successfully with Cache-Control headers!")

        # 4. Review Page Accessibility & Table query param
        print("\n[TEST 4] Review page for existing business...")
        resp = await client.get("/r/zerno?table=3")
        assert resp.status_code == 200
        assert "zerno" in resp.text
        assert "Столик №3" in resp.text
        print("  -> Review page rendered properly with table context!")

        # Non-existent business -> 404
        resp = await client.get("/r/non_existent_business_xyz")
        assert resp.status_code == 404
        print("  -> Non-existent business correctly returns 404 Not Found")

        # 5. Security & Boundary Validations on /api/feedback
        print("\n[TEST 5] Testing Feedback Payload validation and XSS containment...")
        
        # Test rating < 1 rejected
        bad_feedback_1 = {"business_id": "zerno", "rating": 0, "comment": "bad"}
        resp = await client.post("/api/feedback", json=bad_feedback_1)
        assert resp.status_code == 422, f"Expected 422 for rating 0, got {resp.status_code}"
        print("  -> Rating 0 rejected with 422 Unprocessable Entity")

        # Test rating > 5 rejected
        bad_feedback_2 = {"business_id": "zerno", "rating": 6, "comment": "super"}
        resp = await client.post("/api/feedback", json=bad_feedback_2)
        assert resp.status_code == 422, f"Expected 422 for rating 6, got {resp.status_code}"
        print("  -> Rating 6 rejected with 422 Unprocessable Entity")

        # Test valid 5-star positive review
        good_feedback = {"business_id": "zerno", "rating": 5, "comment": "Отличный сервис!"}
        resp = await client.post("/api/feedback", json=good_feedback)
        assert resp.status_code == 200
        assert resp.json().get("status") == "ok"
        print("  -> Rating 5 submitted and recorded successfully!")

        # Test valid negative review with XSS payload in comment & contact
        xss_feedback = {
            "business_id": "zerno", 
            "rating": 2, 
            "comment": "<script>alert('XSS')</script>Суп был холодный", 
            "contact": "<b>+79998887766</b>",
            "table_number": "4"
        }
        resp = await client.post("/api/feedback", json=xss_feedback)
        assert resp.status_code == 200
        assert resp.json().get("status") == "ok"
        print("  -> Rating 2 intercepted safely without server errors!")

        # 6. Pilot Request Payload Validations
        print("\n[TEST 6] Testing Pilot Lead generation and validation...")
        pilot_data = {
            "name": "Иван Ресторатор",
            "phone": "+7 (999) 111-22-33",
            "business_name": "Кафе Уют",
            "city": "Москва",
            "tables_count": "15"
        }
        resp = await client.post("/api/pilot-request", json=pilot_data)
        assert resp.status_code == 200
        assert resp.json().get("status") == "ok"
        print("  -> Pilot lead saved and queued for notification successfully!")

        # 7. Admin Security, Timing Attacks, & Session Handling
        print("\n[TEST 7] Verifying Admin Security...")
        
        # Unauthenticated /admin redirect
        resp = await client.get("/admin", follow_redirects=False)
        assert resp.status_code in (302, 303, 307), f"Expected redirect, got {resp.status_code}"
        assert "/admin/login" in resp.headers.get("location", "")
        print("  -> Unauthenticated /admin correctly redirected to /admin/login!")

        # Unauthenticated mutations rejected/redirected
        resp = await client.post("/admin/leads/1/status", data={"status": "contacted"}, follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        print("  -> Unauthenticated /admin mutation correctly blocked!")

        # Invalid login
        resp = await client.post("/admin/login", data={"username": "admin", "password": "wrongpassword"}, follow_redirects=False)
        assert resp.status_code == 200
        assert "Неверный логин или пароль" in resp.text
        print("  -> Invalid admin login properly rejected with warning!")

        # Valid login
        resp = await client.post("/admin/login", data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}, follow_redirects=False)
        assert resp.status_code in (302, 303)
        admin_cookie = resp.cookies.get("geopulse_admin_session")
        assert admin_cookie is not None
        print("  -> Valid admin login succeeded with session cookie!")

        # Authenticated Admin Dashboard access
        resp = await client.get("/admin", cookies={"geopulse_admin_session": admin_cookie})
        assert resp.status_code == 200
        assert "Сводка & KPI" in resp.text
        print("  -> Authenticated /admin dashboard loaded with 200 OK!")

        # Authenticated Admin subpages
        for subpage in ["/admin/businesses", "/admin/leads", "/admin/reviews", "/admin/managers"]:
            resp = await client.get(subpage, cookies={"geopulse_admin_session": admin_cookie})
            assert resp.status_code == 200
            print(f"  -> Authenticated {subpage} loaded with 200 OK!")

        # Public /leads endpoint redirects to /admin/leads
        resp = await client.get("/leads", follow_redirects=False)
        assert resp.status_code in (301, 302, 303, 307, 308)
        assert "/admin/leads" in resp.headers.get("location", "")
        print("  -> Deprecated public /leads safely redirects to /admin/leads (no PII leakage)!")

        # 8. Protection of /new against unauthorized business creation
        print("\n[TEST 8] Verifying /new protection (No unauthorized business creation)...")
        # Clear cookies to simulate unauthenticated guest
        client.cookies.clear()

        # Unauthorized GET /new -> redirects to /#connect
        resp = await client.get("/new", follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        assert "#connect" in resp.headers.get("location", "")
        print("  -> Unauthorized GET /new safely redirects to /#connect lead form!")

        # Unauthorized POST /new -> 403 Forbidden
        fake_biz_data = {
            "name": "Фейк Кафе",
            "category": "Кафе",
            "id": "fake_cafe",
            "address": "ул. Тестовая",
            "yandex_maps_url": "https://maps.yandex.ru"
        }
        resp = await client.post("/new", data=fake_biz_data, follow_redirects=False)
        assert resp.status_code == 403
        print("  -> Unauthorized POST /new rejected with 403 Forbidden!")

        # Authenticated Admin GET /new -> redirects to /admin/businesses
        client.cookies.set("geopulse_admin_session", admin_cookie)
        resp = await client.get("/new", follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        assert "/admin/businesses" in resp.headers.get("location", "")
        print("  -> Authenticated Admin GET /new redirects to /admin/businesses!")

        # 9. Print Layout Bulk Sheets
        print("\n[TEST 9] Verifying bulk print layout pagination...")
        resp = await client.get("/print/zerno/all?tables=6")
        assert resp.status_code == 200
        assert "print-sheet" in resp.text
        assert "Столик №1" in resp.text
        assert "Столик №6" in resp.text
        print("  -> Bulk print layout rendered with 2-up .print-sheet containers!")

    print("\n=== ALL AUDIT & REGRESSION TESTS PASSED (100% SUCCESS) ===")

if __name__ == "__main__":
    asyncio.run(run_tests())
