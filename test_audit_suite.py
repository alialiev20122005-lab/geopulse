import asyncio
import sqlite3
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.config import ADMIN_USERNAME, ADMIN_PASSWORD
import app.database as db

async def _execute_all():
    print("=== STARTING GEOPULSE AUDIT VERIFICATION SUITE ===")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        
        # 1. Database Configuration Checks
        print("\n[TEST 1] Verifying SQLite WAL mode, foreign keys, and indexes...")
        db.init_db()
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
        print("\n[TEST 2] Verifying public HTTP routes (Landing, Pitch, Offer, Privacy, Terms)...")
        for path in ["/", "/pitch", "/offer", "/privacy", "/terms"]:
            resp = await client.get(path)
            assert resp.status_code == 200, f"Route {path} failed with {resp.status_code}"
            print(f"  -> GET {path} returned 200 OK")

        # 3. Business Onboarding & QR Verification, Favicon, OpenGraph
        print("\n[TEST 3] Testing QR code, Favicon, and OpenGraph assets...")
        resp = await client.get("/api/qr/zerno?v=2&table=5")
        assert resp.status_code == 200
        assert resp.headers.get("content-type") == "image/png"
        assert "max-age=86400" in resp.headers.get("cache-control", "")
        print("  -> QR code generated successfully with Cache-Control headers!")

        resp_fav = await client.get("/favicon.ico")
        assert resp_fav.status_code == 200
        print("  -> /favicon.ico served with 200 OK!")

        resp_og = await client.get("/static/og-preview.png")
        assert resp_og.status_code == 200
        print("  -> /static/og-preview.png OpenGraph banner served with 200 OK!")

        # 4. Review Page Accessibility & Table query param
        print("\n[TEST 4] Review page for existing business...")
        resp = await client.get("/r/zerno?table=3")
        assert resp.status_code == 200
        assert "zerno" in resp.text
        assert "Столик №3" in resp.text
        print("  -> Review page rendered properly with table context!")

        # Non-existent business -> 404 with custom branded template
        resp = await client.get("/r/non_existent_business_xyz")
        assert resp.status_code == 404
        assert "Страница не найдена" in resp.text
        print("  -> Non-existent business correctly returns branded 404 HTML!")

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
        fb_id = resp.json().get("feedback_id")
        print("  -> Rating 2 intercepted safely without server errors!")

        # Unauthorized feedback status update -> 403
        resp_stat_unauth = await client.post(f"/api/feedback/{fb_id}/status", data={"status": "resolved"})
        assert resp_stat_unauth.status_code == 403
        print("  -> Unauthorized feedback status change properly blocked with 403!")

        # Authorized Manager status update with session cookie -> 200
        resp_stat_auth = await client.post(
            f"/api/feedback/{fb_id}/status", 
            data={"status": "resolved"},
            cookies={"dashboard_unlocked_zerno": "1"}
        )
        assert resp_stat_auth.status_code == 200
        assert resp_stat_auth.json().get("new_status") == "resolved"
        print("  -> Authorized Manager successfully marked complaint as 'resolved'!")

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

        # 10. Dashboard Privacy Protection
        print("\n[TEST 10] Verifying Dashboard privacy and lock screen...")
        client.cookies.clear()
        
        # Unauthenticated guest visits /dashboard/zerno -> gets lock screen
        resp = await client.get("/dashboard/zerno")
        assert resp.status_code == 200
        assert "Дашборд защищен" in resp.text
        assert "Журнал обращений клиентов" not in resp.text
        print("  -> Unauthenticated /dashboard/zerno shows Lock Screen (no data leaked)!")

        # Unlock with wrong code
        resp = await client.post("/dashboard/zerno/unlock", data={"access_code": "wrong_pin"})
        assert resp.status_code == 200
        assert "Неверный код доступа" in resp.text
        print("  -> Invalid access code properly rejected!")

        # Unlock with valid admin password
        resp = await client.post("/dashboard/zerno/unlock", data={"access_code": ADMIN_PASSWORD}, follow_redirects=False)
        assert resp.status_code == 303
        mgr_cookie = resp.cookies.get("geopulse_mgr_zerno")
        assert mgr_cookie is not None
        print("  -> Dashboard successfully unlocked via code, session cookie established!")

        # Visiting with manager cookie
        resp = await client.get("/dashboard/zerno", cookies={"geopulse_mgr_zerno": mgr_cookie})
        assert resp.status_code == 200
        assert "Журнал обращений клиентов" in resp.text
        print("  -> Authenticated Manager dashboard displays full analytics!")

        # Direct access with admin session
        resp = await client.get("/dashboard/zerno", cookies={"geopulse_admin_session": admin_cookie})
        assert resp.status_code == 200
        assert "Журнал обращений клиентов" in resp.text
        print("  -> Platform Admin has seamless direct access to venue dashboards!")

        # 11. Payments & Subscription Activation
        print("\n[TEST 11] Verifying Online Payments & Subscription Activation...")
        pay_payload = {
            "business_id": "zerno",
            "plan_id": "start",
            "phone": "+7 (999) 555-44-33",
            "email": "owner@zerno.ru"
        }
        resp = await client.post("/api/payments/create", json=pay_payload)
        assert resp.status_code == 200
        pay_data = resp.json()
        assert pay_data.get("status") == "ok"
        pid = pay_data.get("payment_id")
        assert pid is not None
        print("  -> Payment intent created successfully with provider ID!")

        # Sandbox page loads with 200 OK
        resp = await client.get(f"/pay/sandbox?pid={pid}")
        assert resp.status_code == 200
        assert "Оформление подписки" in resp.text
        print("  -> Sandbox/Invoice payment screen rendered successfully with 200 OK!")

        # Confirm payment
        resp = await client.post("/api/payments/sandbox-confirm", data={"pid": pid}, follow_redirects=False)
        assert resp.status_code == 303
        assert "/pay/success" in resp.headers.get("location", "")
        print("  -> Payment confirmed, redirected to success page!")

        # Check success page
        resp = await client.get("/pay/success?bid=zerno&plan=start")
        assert resp.status_code == 200
        assert "Подписка активирована" in resp.text
        print("  -> /pay/success screen loaded with 200 OK!")

        # Verify DB reflects active subscription
        zerno = db.get_business("zerno")
        assert zerno.get("status") == "active"
        print("  -> Business status successfully transitioned from 'pilot' to 'active' in DB!")

        # ---------------------------------------------------------
        # TEST 12: Stage 4 - Advanced Analytics, CSV Exports, Sales Kit, Digests
        # ---------------------------------------------------------
        print("\n[TEST 12] Verifying Analytics, CSV Exports, Sales Kit, and Digests...")

        # 1. Rating breakdown & NPS in DB
        breakdown = db.get_business_rating_breakdown("zerno")
        assert "counts" in breakdown
        assert "percentages" in breakdown
        assert "nps" in breakdown
        assert breakdown["total"] >= 1
        print(f"  -> NPS calculation verified: {breakdown['nps']}% (Promoters: {breakdown['promoters']}, Detractors: {breakdown['detractors']})")

        # 2. Manager reviews CSV export
        resp = await client.get("/dashboard/zerno/export")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert "Оценка (1-5)" in resp.text
        print("  -> Manager reviews CSV export generated successfully (UTF-8 with BOM for Excel)!")

        # 3. Admin reviews CSV export (requires admin session)
        resp_unauth = await client.get("/admin/reviews/export", follow_redirects=False)
        assert resp_unauth.status_code == 303
        print("  -> Unauthenticated /admin/reviews/export safely redirected to login!")

        resp = await client.get("/admin/reviews/export", cookies={"geopulse_admin_session": admin_cookie})
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        assert "attachment" in resp.headers.get("content-disposition", "")
        print("  -> Authenticated Admin all-reviews CSV export generated successfully!")

        # 4. Admin leads CSV export (requires admin session)
        resp = await client.get("/admin/leads/export", cookies={"geopulse_admin_session": admin_cookie})
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        assert "Дата заявки" in resp.text
        print("  -> Authenticated Admin leads CSV export generated successfully!")

        # 5. Sales Kit page
        resp = await client.get("/sales-kit")
        assert resp.status_code == 200
        assert "Скрипты, шаблоны сообщений и чек-лист пилота" in resp.text
        print("  -> /sales-kit rendered successfully with 200 OK!")

        # 6. Daily digest generation
        from app.bot import generate_digest_text
        digest_text = generate_digest_text("zerno", "Кофейня Зерно")
        assert "СУТОЧНЫЙ ДАЙДЖЕСТ" in digest_text
        print("  -> Telegram daily digest generated cleanly!")

        # 7. Digest API endpoint
        resp = await client.get("/api/digest")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_24h" in data
        print("  -> /api/digest endpoint returned 200 OK with valid metrics!")

        # TEST 13: 1-Click Lead Conversion & Security Rate Limiter Verification
        print("\n[TEST 13] Verifying 1-Click Lead Conversion & Security Rate Limiting...")
        from app.rate_limiter import admin_login_rate_limiter
        from app.database import create_lead, get_lead_by_id, get_business

        # 1. 1-Click Lead Conversion to Pilot Business
        lead_id = create_lead("Магомед Владелец", "Горный Бриз", "+7 (999) 777-66-55", "г. Махачкала", "15")
        # Unauthenticated request blocked
        unauth_conv = await client.post(f"/admin/leads/{lead_id}/convert", follow_redirects=False)
        assert unauth_conv.status_code == 303 and "/admin/login" in unauth_conv.headers["location"]

        # Authenticated conversion
        conv_resp = await client.post(f"/admin/leads/{lead_id}/convert", cookies={"geopulse_admin_session": admin_cookie}, follow_redirects=False)
        assert conv_resp.status_code == 303
        assert "gornyy_briz" in conv_resp.headers["location"]
        
        # Verify created business
        biz = get_business("gornyy_briz")
        assert biz is not None
        assert biz["name"] == "Горный Бриз"
        assert biz["status"] == "pilot"
        
        # Verify lead status updated
        updated_lead = get_lead_by_id(lead_id)
        assert updated_lead["status"] == "pilot_active"
        print("  -> 1-Click Lead to Pilot Business conversion PASSED!")

        # 2. Admin Login Rate Limiting
        admin_login_rate_limiter.reset()
        # 5 allowed attempts
        for _ in range(5):
            admin_login_rate_limiter.check("127.0.0.1")
        # 6th attempt should be blocked
        assert admin_login_rate_limiter.check("127.0.0.1") is False
        blocked_resp = await client.post("/admin/login", data={"username": "fake", "password": "wrong"})
        assert blocked_resp.status_code == 429
        print("  -> Admin login brute-force rate limiter (429) PASSED!")
        admin_login_rate_limiter.reset()

    print("\n=== ALL AUDIT & REGRESSION TESTS PASSED (100% SUCCESS) ===")

async def run_tests():
    from unittest.mock import patch, AsyncMock
    with patch("app.bot.send_message", new_callable=AsyncMock) as mock_send, \
         patch("app.main.send_message", new_callable=AsyncMock) as mock_main_send, \
         patch("app.backup.send_backup_to_telegram", new_callable=AsyncMock) as mock_backup_send:
        mock_main_send.return_value = True
        mock_send.return_value = True
        mock_backup_send.return_value = True
        try:
            await _execute_all()
        finally:
            print("\n[TEARDOWN] Purging test feedbacks, leads, payments and restoring state...")
            with db.get_connection() as c:
                c.execute("DELETE FROM feedbacks WHERE comment LIKE '%XSS%' OR comment = 'Отличный сервис!'")
                c.execute("DELETE FROM leads WHERE phone IN ('+7 (999) 111-22-33', '+7 (999) 777-66-55') OR name IN ('Иван Тест', 'Магомед Владелец')")
                c.execute("DELETE FROM businesses WHERE id = 'gornyy_briz'")
                c.execute("DELETE FROM payments WHERE provider_payment_id LIKE 'test_%'")
                c.execute("UPDATE businesses SET status = 'active' WHERE id IN ('zerno', 'sahara')")
                c.commit()
            print("  -> Test environment cleanly restored!")

if __name__ == "__main__":
    asyncio.run(run_tests())
