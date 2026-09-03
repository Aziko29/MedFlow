# tests/test_prompt23_part2_breadcrumb.py
"""
PROMPT 23, 2-band — Bemor, Shifokor va Qabul sahifalarida Breadcrumb
komponenti ("Bosh sahifa > ... > joriy sahifa") render bo'lishini
tekshiradi.
"""


class TestBreadcrumbOnPatientDetail:
    def test_breadcrumb_shows_home_list_and_fullname(
        self, client, db, make_user, make_patient, login_as
    ):
        admin = make_user("admin")
        patient = make_patient(fullname="Aliyev Vali Aliyevich")
        login_as(admin)

        resp = client.get(f"/patients/{patient.id}")
        assert resp.status_code == 200
        html = resp.text
        assert 'class="breadcrumb"' in html
        assert 'href="/dashboard"' in html
        assert 'href="/patients"' in html
        assert "Aliyev Vali Aliyevich" in html
        # Joriy (oxirgi) band havolasiz, oddiy matn sifatida chiqishi kerak
        assert '<span class="breadcrumb-item current"' in html


class TestBreadcrumbOnDoctorDetail:
    def test_breadcrumb_shows_home_list_and_fullname(
        self, client, db, make_user, make_doctor, login_as
    ):
        admin = make_user("admin")
        doctor = make_doctor(fullname="Karimov Sardor Baxtiyorovich")
        login_as(admin)

        resp = client.get(f"/doctors/{doctor.id}")
        assert resp.status_code == 200
        html = resp.text
        assert 'class="breadcrumb"' in html
        assert 'href="/dashboard"' in html
        assert 'href="/doctors"' in html
        assert "Karimov Sardor Baxtiyorovich" in html


class TestBreadcrumbOnAppointmentsPage:
    def test_breadcrumb_shows_home_and_current_page(
        self, client, db, make_user, login_as
    ):
        admin = make_user("admin")
        login_as(admin)

        resp = client.get("/appointments")
        assert resp.status_code == 200
        html = resp.text
        assert 'class="breadcrumb"' in html
        assert 'href="/dashboard"' in html
        assert '<span class="breadcrumb-item current" aria-current="page">Qabul</span>' in html
