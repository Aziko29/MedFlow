# tests/test_prompt23_patient_tab_hash_render.py
"""
PROMPT 23, 1-band — sanity: patient_detail.html hali ham to'g'ri render
bo'lishini va yangi tab/hash JS'ini o'z ichiga olishini tekshiradi.
"""


class TestPatientDetailPageRendersWithTabHashJS:
    def test_admin_can_render_patient_detail_with_hash_sync_js(
        self, client, db, make_user, make_patient, login_as
    ):
        admin = make_user("admin")
        patient = make_patient()
        login_as(admin)

        resp = client.get(f"/patients/{patient.id}")
        assert resp.status_code == 200
        html = resp.text
        assert "applyPatientTabFromHash" in html
        assert "hashchange" in html
        assert "PATIENT_TABS" in html

    def test_cashier_can_render_patient_detail_without_error(
        self, client, db, make_user, make_patient, login_as
    ):
        """Cashier uchun 'tibbiy'/'davolanish'/'tahlil' tablari DOM'da
        yo'q — sahifa baribir xatosiz render bo'lishi kerak."""
        cashier = make_user("cashier")
        patient = make_patient()
        login_as(cashier)

        resp = client.get(f"/patients/{patient.id}")
        assert resp.status_code == 200
        assert "applyPatientTabFromHash" in resp.text
