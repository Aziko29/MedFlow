# tests/test_patient_admission_model.py
"""
PROMPT 9 — Bemor yotqizilishi: PatientAdmission modeli.

Bag: Patient.room_number/admitted_at/discharged_at/is_admitted (Prompt 7)
— oddiy Column'lar bo'lgani uchun, bemor chiqarilib keyin QAYTA
yotqizilganda ular ustiga yozib yuborilardi va oldingi yotqizilishning
`discharged_at`si (tarixi) butunlay yo'qolardi.

Ushbu testlar yangi `models.PatientAdmission` jadvali/relationship'i:
  1. To'g'ri yaratilishini va barcha maydonlarni (id, patient_id,
     room_number, admitted_at, discharged_at, discharged_reason) saqlab
     qolishini,
  2. `Patient.admissions` orqali bog'lanishini (va teskarisi —
     `PatientAdmission.patient`),
  3. Bir nechta admission yozuvi (qayta yotqizilish) BIR-BIRINI
     O'CHIRMASDAN, alohida qatorlar sifatida saqlanishini (aynan shu
     Prompt tuzatayotgan bag),
  4. Patient'dagi joriy-holat ustunlari (is_admitted va h.k.) hamon
     mavjud va ishlayotganini (talab #3: ular saqlab qolinishi kerak)
tekshiradi.
"""
import datetime

import models


class TestPatientAdmissionModelCreation:
    def test_creates_admission_with_all_fields(self, db, make_patient):
        patient = make_patient()
        admitted = datetime.datetime(2026, 1, 10, 9, 0, 0)
        discharged = datetime.datetime(2026, 1, 15, 12, 0, 0)

        admission = models.PatientAdmission(
            patient_id=patient.id,
            room_number="204",
            admitted_at=admitted,
            discharged_at=discharged,
            discharged_reason="Sog'aydi",
        )
        db.add(admission)
        db.commit()
        db.refresh(admission)

        assert admission.id is not None
        assert admission.patient_id == patient.id
        assert admission.room_number == "204"
        assert admission.admitted_at == admitted
        assert admission.discharged_at == discharged
        assert admission.discharged_reason == "Sog'aydi"

    def test_discharged_fields_nullable_while_still_admitted(self, db, make_patient):
        """Bemor hali chiqarilmagan bo'lsa, discharged_at/discharged_reason
        NULL bo'lishi kerak (bo'sh string emas)."""
        patient = make_patient()
        admission = models.PatientAdmission(
            patient_id=patient.id,
            room_number="101",
            admitted_at=datetime.datetime.utcnow(),
        )
        db.add(admission)
        db.commit()
        db.refresh(admission)

        assert admission.discharged_at is None
        assert admission.discharged_reason is None

    def test_admitted_at_required(self, db, make_patient):
        """admitted_at NOT NULL — DB darajasida majburiy."""
        patient = make_patient()
        admission = models.PatientAdmission(patient_id=patient.id, admitted_at=None)
        db.add(admission)
        try:
            db.commit()
            committed_without_error = True
        except Exception:
            committed_without_error = False
            db.rollback()
        assert committed_without_error is False


class TestPatientAdmissionRelationship:
    def test_patient_admissions_relationship_returns_created_rows(
        self, db, make_patient
    ):
        patient = make_patient()
        admission = models.PatientAdmission(
            patient_id=patient.id,
            room_number="305",
            admitted_at=datetime.datetime.utcnow(),
        )
        db.add(admission)
        db.commit()

        db.expire_all()
        row = db.query(models.Patient).filter(models.Patient.id == patient.id).first()

        assert len(row.admissions) == 1
        assert row.admissions[0].room_number == "305"

    def test_admission_patient_backref(self, db, make_patient):
        patient = make_patient()
        admission = models.PatientAdmission(
            patient_id=patient.id,
            admitted_at=datetime.datetime.utcnow(),
        )
        db.add(admission)
        db.commit()
        db.refresh(admission)

        assert admission.patient is not None
        assert admission.patient.id == patient.id

    def test_multiple_admissions_preserve_full_history(self, db, make_patient):
        """Aynan tuzatilayotgan bag: bemor bir necha marta yotqizilib-
        chiqarilsa, HAR BIR voqea alohida qator sifatida qoladi — biri
        ikkinchisini o'chirib yubormaydi."""
        patient = make_patient()

        first = models.PatientAdmission(
            patient_id=patient.id,
            room_number="101",
            admitted_at=datetime.datetime(2026, 1, 1, 9, 0, 0),
            discharged_at=datetime.datetime(2026, 1, 5, 10, 0, 0),
            discharged_reason="Sog'aydi",
        )
        second = models.PatientAdmission(
            patient_id=patient.id,
            room_number="204",
            admitted_at=datetime.datetime(2026, 2, 1, 9, 0, 0),
            discharged_at=datetime.datetime(2026, 2, 3, 10, 0, 0),
            discharged_reason="Boshqa klinikaga o'tkazildi",
        )
        db.add_all([first, second])
        db.commit()

        db.expire_all()
        row = db.query(models.Patient).filter(models.Patient.id == patient.id).first()

        assert len(row.admissions) == 2
        # order_by="PatientAdmission.admitted_at.desc()" — eng yangisi birinchi.
        assert row.admissions[0].room_number == "204"
        assert row.admissions[0].discharged_reason == "Boshqa klinikaga o'tkazildi"
        assert row.admissions[1].room_number == "101"
        assert row.admissions[1].discharged_reason == "Sog'aydi"
        # ✅ Birinchi yotqizilishning discharged_at'i ikkinchisi qo'shilgach
        # ham SAQLANIB QOLGAN — bag endi tuzatildi.
        assert row.admissions[1].discharged_at == datetime.datetime(2026, 1, 5, 10, 0, 0)


class TestPatientCurrentStateColumnsStillWork:
    """Talab #3: Patient'dagi joriy holatni ko'rsatuvchi ustunlar
    (room_number/admitted_at/discharged_at/is_admitted) admissions
    relationship qo'shilgandan keyin ham saqlanib qolgan va ishlayotgan
    bo'lishi kerak."""

    def test_patient_still_has_current_state_columns(self, db, make_patient):
        patient = make_patient()
        patient.room_number = "410"
        patient.admitted_at = datetime.datetime.utcnow()
        patient.is_admitted = True
        db.commit()
        db.refresh(patient)

        assert patient.room_number == "410"
        assert patient.is_admitted is True
        assert patient.discharged_at is None

    def test_current_state_columns_independent_of_admissions_table(
        self, db, make_patient
    ):
        """admissions jadvali bo'sh bo'lsa ham, joriy-holat ustunlari
        (eski mexanizm) hamon mustaqil ishlaydi — orqaga qarab moslik."""
        patient = make_patient()
        patient.is_admitted = False
        db.commit()
        db.refresh(patient)

        assert patient.admissions == []
        assert patient.is_admitted is False
