# lab_templates.py
"""
🔬 Lab tahlil shablonlari — 20 ta standart tahlil turi, har biri o'z
ko'rsatkichlari (indikatorlari), o'lchov birligi, me'yoriy oralig'i va
"standart" (me'yoriy) qiymati bilan.

MAQSAD: shifokor/hamshira har safar 20+ ta ko'rsatkichni noldan
qo'lda kiritmasin. Tahlil turini tanlagach, barcha ko'rsatkichlar
standart (me'yoriy) qiymat bilan oldindan to'ldirilgan holda chiqadi —
shifokor faqat o'zgargan (patologik) ko'rsatkichlarni tahrirlaydi.

Bu modul FAQAT ma'lumot va sof funksiyalardan iborat — DB yoki
FastAPI'ga bog'liq emas, shuning uchun uni modules/lab_results.py
tashqarisida ham (masalan testlarda) mustaqil ishlatish mumkin.

Ko'rsatkich (indicator) turlari:
  - "number": raqamli qiymat. ref_min/ref_max — me'yoriy oraliq (validatsiya
    va avtomatik bayroq — past/yuqori/me'yorda — uchun ishlatiladi).
  - "select": tayinlangan variantlar ro'yxatidan tanlanadi (masalan
    "Manfiy"/"Musbat"). normal_values — me'yoriy hisoblanadigan
    variant(lar); ulardan tashqarisi "ogohlantirish" deb belgilanadi.
  - "choice": tanlov, lekin "me'yor"/"patologiya" tushunchasi yo'q
    (masalan qon guruhi) — hech qachon bayroq qo'yilmaydi.
  - "text": erkin matn, bayroqlanmaydi.

Yangi tahlil turini qo'shish uchun shunchaki LAB_TEMPLATES lug'atiga
yangi kalit qo'shish kifoya — modul va shablon avtomatik ro'yxatga
chiqadi.
"""
from typing import Any, Dict, List, Optional, Tuple

NEGATIVE_POSITIVE = ["Manfiy", "Musbat"]
YES_NO = ["Yo'q", "Bor"]
FOUND_NOT_FOUND = ["Topilmadi", "Topildi"]


def _num(key: str, name: str, unit: str, ref_min: float, ref_max: float, default: float, step: float = 0.1) -> Dict[str, Any]:
    return {
        "key": key, "name": name, "type": "number", "unit": unit,
        "ref_min": ref_min, "ref_max": ref_max, "default": default, "step": step,
        "ref_range": f"{ref_min:g}–{ref_max:g} {unit}".strip(),
    }


def _sel(key: str, name: str, options: List[str], normal_values: List[str], default: Optional[str] = None) -> Dict[str, Any]:
    return {
        "key": key, "name": name, "type": "select", "unit": None,
        "options": options, "normal_values": normal_values,
        "default": default if default is not None else normal_values[0],
        "ref_range": "me'yor: " + ", ".join(normal_values),
    }


def _choice(key: str, name: str, options: List[str], default: Optional[str] = None) -> Dict[str, Any]:
    return {
        "key": key, "name": name, "type": "choice", "unit": None,
        "options": options, "default": default if default is not None else options[0],
        "ref_range": "—",
    }


def _text(key: str, name: str, default: str = "—") -> Dict[str, Any]:
    return {"key": key, "name": name, "type": "text", "unit": None, "default": default, "ref_range": "—"}


LAB_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "cbc": {
        "name": "Umumiy qon tahlili", "category": "Gematologiya",
        "indicators": [
            _num("hgb", "Gemoglobin (HGB)", "g/l", 130, 160, 145, 1),
            _num("rbc", "Eritrotsitlar (RBC)", "10¹²/l", 4.0, 5.5, 4.8, 0.1),
            _num("wbc", "Leykotsitlar (WBC)", "10⁹/l", 4.0, 9.0, 6.0, 0.1),
            _num("plt", "Trombotsitlar (PLT)", "10⁹/l", 150, 400, 250, 1),
            _num("hct", "Gematokrit (HCT)", "%", 36, 48, 42, 0.5),
            _num("esr", "ECHT (SOE)", "mm/soat", 2, 15, 8, 1),
            _num("eos", "Eozinofillar", "%", 0, 5, 2, 1),
            _num("lymph", "Limfotsitlar", "%", 19, 37, 28, 1),
            _num("neut", "Neytrofillar", "%", 47, 72, 60, 1),
        ],
    },
    "urine": {
        "name": "Umumiy siydik tahlili", "category": "Umumiy tahlillar",
        "indicators": [
            _sel("color", "Rangi", ["Sarg'ish", "Somonrang", "To'q sariq", "Qizg'ish"], ["Sarg'ish", "Somonrang"]),
            _sel("clarity", "Shaffofligi", ["Tiniq", "Xiralashgan"], ["Tiniq"]),
            _num("sg", "Solishtirma og'irlik", "", 1.010, 1.025, 1.018, 0.001),
            _num("ph", "pH", "", 5.0, 7.0, 6.0, 0.1),
            _num("protein", "Oqsil", "g/l", 0, 0.14, 0, 0.01),
            _num("glucose", "Glyukoza", "mmol/l", 0, 0.8, 0, 0.1),
            _sel("ketone", "Keton tanachalari", NEGATIVE_POSITIVE, ["Manfiy"]),
            _num("leu", "Leykotsitlar (k/m)", "ko'rish maydonida", 0, 3, 1, 1),
            _num("ery", "Eritrotsitlar (k/m)", "ko'rish maydonida", 0, 2, 0, 1),
            _num("epi", "Epiteliy hujayralari", "ko'rish maydonida", 0, 5, 2, 1),
            _sel("bacteria", "Bakteriyalar", YES_NO, ["Yo'q"]),
        ],
    },
    "biochem": {
        "name": "Qon biokimyoviy tahlili", "category": "Biokimyo",
        "indicators": [
            _num("tp", "Umumiy oqsil", "g/l", 64, 83, 74, 1),
            _num("alb", "Albumin", "g/l", 35, 52, 44, 1),
            _num("tbil", "Umumiy bilirubin", "mkmol/l", 3.4, 20.5, 12, 0.5),
            _num("dbil", "To'g'ridan bilirubin", "mkmol/l", 0, 5.1, 3, 0.5),
            _num("alt", "ALT", "U/l", 0, 41, 20, 1),
            _num("ast", "AST", "U/l", 0, 40, 20, 1),
            _num("creat", "Kreatinin", "mkmol/l", 62, 115, 80, 1),
            _num("urea", "Mochevina", "mmol/l", 2.5, 8.3, 5, 0.1),
            _num("glu", "Glyukoza", "mmol/l", 3.9, 6.1, 5, 0.1),
            _num("chol", "Umumiy xolesterin", "mmol/l", 3.0, 5.2, 4.2, 0.1),
        ],
    },
    "blood_group": {
        "name": "Qon guruhi va Rezus omili", "category": "Umumiy tahlillar",
        "indicators": [
            _choice("group", "Qon guruhi", ["O (I)", "A (II)", "B (III)", "AB (IV)"]),
            _choice("rh", "Rezus omili", ["Rh(+) musbat", "Rh(-) manfiy"]),
        ],
    },
    "coagulogram": {
        "name": "Qon ivish tizimi (Koagulogramma)", "category": "Gematologiya",
        "indicators": [
            _num("pt", "Protrombin vaqti (PT)", "soniya", 11, 15, 13, 0.5),
            _num("inr", "INR", "nisbat", 0.8, 1.2, 1.0, 0.05),
            _num("fib", "Fibrinogen", "g/l", 2.0, 4.0, 3.0, 0.1),
            _num("clot_time", "Qon ivish vaqti", "daqiqa", 5, 10, 7, 1),
            _num("bleed_time", "Qon ketish vaqti", "daqiqa", 2, 5, 3, 1),
        ],
    },
    "liver": {
        "name": "Jigar funksional sinamalari", "category": "Biokimyo",
        "indicators": [
            _num("alt", "ALT", "U/l", 0, 41, 20, 1),
            _num("ast", "AST", "U/l", 0, 40, 20, 1),
            _num("alp", "Ishqoriy fosfataza (ALP)", "U/l", 40, 150, 90, 1),
            _num("ggt", "GGT", "U/l", 0, 55, 25, 1),
            _num("tbil", "Umumiy bilirubin", "mkmol/l", 3.4, 20.5, 12, 0.5),
            _num("tp", "Umumiy oqsil", "g/l", 64, 83, 74, 1),
            _num("alb", "Albumin", "g/l", 35, 52, 44, 1),
        ],
    },
    "renal": {
        "name": "Buyrak funksional sinamalari", "category": "Biokimyo",
        "indicators": [
            _num("creat", "Kreatinin", "mkmol/l", 62, 115, 80, 1),
            _num("urea", "Mochevina", "mmol/l", 2.5, 8.3, 5, 0.1),
            _num("uric", "Siydik kislotasi", "mkmol/l", 155, 357, 250, 1),
            _num("gfr", "GFR (SKF)", "ml/min/1.73m²", 90, 120, 100, 1),
            _num("k", "Kaliy (K)", "mmol/l", 3.5, 5.1, 4.3, 0.1),
            _num("na", "Natriy (Na)", "mmol/l", 136, 145, 140, 1),
        ],
    },
    "lipid": {
        "name": "Lipid profil", "category": "Biokimyo",
        "indicators": [
            _num("chol", "Umumiy xolesterin", "mmol/l", 3.0, 5.2, 4.2, 0.1),
            _num("ldl", "LDL (yomon xolesterin)", "mmol/l", 0, 3.0, 2.4, 0.1),
            _num("hdl", "HDL (yaxshi xolesterin)", "mmol/l", 1.0, 2.2, 1.4, 0.1),
            _num("tg", "Trigliseridlar", "mmol/l", 0.4, 1.7, 1.1, 0.1),
            _num("ai", "Aterogen indeks", "nisbat", 2.0, 3.0, 2.5, 0.1),
        ],
    },
    "glucose_panel": {
        "name": "Qandli diabet nazorati", "category": "Endokrinologiya",
        "indicators": [
            _num("fbg", "Glyukoza (och qoringa)", "mmol/l", 3.9, 6.1, 5.0, 0.1),
            _num("hba1c", "HbA1c (glikirlangan gemoglobin)", "%", 4.0, 5.6, 5.0, 0.1),
            _num("ppg", "Ovqatdan keyingi glyukoza (2 soat)", "mmol/l", 3.9, 7.8, 6.0, 0.1),
            _num("insulin", "Insulin", "mkED/ml", 2.6, 24.9, 10, 0.5),
        ],
    },
    "thyroid": {
        "name": "Qalqonsimon bez gormonlari", "category": "Endokrinologiya",
        "indicators": [
            _num("tsh", "TSH", "mED/l", 0.4, 4.0, 2.0, 0.1),
            _num("ft3", "T3 (erkin)", "pmol/l", 3.5, 6.5, 5.0, 0.1),
            _num("ft4", "T4 (erkin)", "pmol/l", 9.0, 20.0, 14, 0.5),
            _num("anti_tpo", "Anti-TPO", "U/ml", 0, 34, 10, 1),
        ],
    },
    "hepatitis": {
        "name": "Gepatit markerlari", "category": "Infeksiya markerlari",
        "indicators": [
            _sel("hbsag", "HBsAg (Gepatit B)", NEGATIVE_POSITIVE, ["Manfiy"]),
            _sel("anti_hcv", "Anti-HCV (Gepatit C)", NEGATIVE_POSITIVE, ["Manfiy"]),
            _sel("anti_hav", "Anti-HAV IgM (Gepatit A)", NEGATIVE_POSITIVE, ["Manfiy"]),
        ],
    },
    "hiv": {
        "name": "OIV (HIV) tahlili", "category": "Infeksiya markerlari",
        "indicators": [
            _sel("hiv", "HIV 1/2 antikor/antigen", NEGATIVE_POSITIVE, ["Manfiy"]),
            _text("method", "Tekshiruv usuli", "ELISA"),
        ],
    },
    "rw": {
        "name": "RW (Sifilis) reaksiyasi", "category": "Infeksiya markerlari",
        "indicators": [
            _sel("rw", "Vassermann reaksiyasi (RW)", NEGATIVE_POSITIVE, ["Manfiy"]),
            _text("titr", "Titr", "—"),
        ],
    },
    "stool": {
        "name": "Umumiy najas tahlili (Koprogramma)", "category": "Umumiy tahlillar",
        "indicators": [
            _sel("consistency", "Konsistensiyasi", ["Qattiq", "Yumshoq", "Suyuq"], ["Yumshoq"]),
            _sel("color", "Rangi", ["Jigarrang", "Sariq", "Yashil", "Oq"], ["Jigarrang"]),
            _sel("occult_blood", "Yashirin qon", NEGATIVE_POSITIVE, ["Manfiy"]),
            _sel("leukocytes", "Leykotsitlar", YES_NO, ["Yo'q"]),
            _sel("parasites", "Gijja tuxumlari (parazitlar)", FOUND_NOT_FOUND, ["Topilmadi"]),
        ],
    },
    "electrolytes": {
        "name": "Elektrolitlar", "category": "Biokimyo",
        "indicators": [
            _num("na", "Natriy (Na)", "mmol/l", 136, 145, 140, 1),
            _num("k", "Kaliy (K)", "mmol/l", 3.5, 5.1, 4.3, 0.1),
            _num("cl", "Xlor (Cl)", "mmol/l", 98, 107, 102, 1),
            _num("ca", "Kalsiy (Ca)", "mmol/l", 2.15, 2.55, 2.35, 0.05),
            _num("mg", "Magniy (Mg)", "mmol/l", 0.66, 1.07, 0.85, 0.01),
        ],
    },
    "inflammation": {
        "name": "C-reaktiv oqsil (CRP) va yallig'lanish markerlari", "category": "Biokimyo",
        "indicators": [
            _num("crp", "CRP (C-reaktiv oqsil)", "mg/l", 0, 5, 2, 0.1),
            _num("esr", "ECHT (SOE)", "mm/soat", 2, 15, 8, 1),
            _num("pct", "Prokalsitonin", "ng/ml", 0, 0.05, 0.02, 0.01),
            _num("fib", "Fibrinogen", "g/l", 2.0, 4.0, 3.0, 0.1),
        ],
    },
    "hormones_f": {
        "name": "Gormonlar paneli (ayollar)", "category": "Endokrinologiya",
        "indicators": [
            _num("fsh", "FSH", "IU/l", 1.5, 12.4, 6, 0.1),
            _num("lh", "LH", "IU/l", 1.7, 8.6, 5, 0.1),
            _num("prl", "Prolaktin", "ng/ml", 4.8, 23.3, 12, 0.1),
            _num("prog", "Progesteron", "ng/ml", 0.2, 25, 5, 0.1),
            _num("e2", "Estradiol", "pg/ml", 12.5, 166, 60, 1),
        ],
    },
    "psa": {
        "name": "Prostata spetsifik antigen (PSA)", "category": "Onkomarkerlar",
        "indicators": [
            _num("total_psa", "Umumiy PSA", "ng/ml", 0, 4.0, 1.5, 0.1),
            _num("free_psa", "Erkin PSA", "ng/ml", 0, 1.0, 0.4, 0.05),
            _num("ratio", "Erkin/umumiy PSA nisbati", "%", 15, 100, 30, 1),
        ],
    },
    "rheumatic": {
        "name": "Revmatik sinamalar", "category": "Immunologiya",
        "indicators": [
            _num("rf", "Revmatoid faktor (RF)", "IU/ml", 0, 14, 8, 1),
            _num("aslo", "ASLO (Antistreptolizin-O)", "IU/ml", 0, 200, 100, 5),
            _num("crp", "CRP", "mg/l", 0, 5, 2, 0.1),
            _num("uric", "Siydik kislotasi", "mkmol/l", 155, 357, 250, 1),
        ],
    },
    "covid": {
        "name": "COVID-19 tahlili", "category": "Infeksiya markerlari",
        "indicators": [
            _sel("pcr", "PCR natijasi", NEGATIVE_POSITIVE, ["Manfiy"]),
            _sel("antigen", "Antigen tez test", NEGATIVE_POSITIVE, ["Manfiy"]),
            _text("ct_value", "Ct qiymati (musbat bo'lsa)", "—"),
        ],
    },
}


def list_templates() -> List[Dict[str, str]]:
    """Shablonlar ro'yxati (dropdown uchun), kategoriya bo'yicha guruhlangan holda ko'rsatish uchun."""
    return [
        {"key": key, "name": tpl["name"], "category": tpl["category"]}
        for key, tpl in LAB_TEMPLATES.items()
    ]


def get_template(template_key: str) -> Optional[Dict[str, Any]]:
    return LAB_TEMPLATES.get(template_key)


def _flag_for(indicator_def: Dict[str, Any], raw_value: str) -> Tuple[str, str]:
    """Berilgan ko'rsatkich ta'rifi va xom qiymat asosida (value_normalized, flag)
    qaytaradi. flag: 'normal' | 'past' | 'yuqori' | 'ogohlantirish' | 'neutral'."""
    kind = indicator_def["type"]

    if kind == "number":
        try:
            val = float(str(raw_value).strip().replace(",", "."))
        except (TypeError, ValueError):
            raise ValueError(f"'{indicator_def['name']}' uchun raqam kiritilishi kerak.")
        ref_min, ref_max = indicator_def["ref_min"], indicator_def["ref_max"]
        if val < ref_min:
            flag = "past"
        elif val > ref_max:
            flag = "yuqori"
        else:
            flag = "normal"
        # Ortiqcha nol/format tozalash: butun son bo'lsa butun ko'rinishda chiqaramiz
        normalized = f"{val:g}"
        return normalized, flag

    if kind == "select":
        options = indicator_def["options"]
        value = str(raw_value).strip()
        if value not in options:
            raise ValueError(f"'{indicator_def['name']}' uchun ro'yxatdagi qiymatlardan biri tanlanishi kerak.")
        flag = "normal" if value in indicator_def["normal_values"] else "ogohlantirish"
        return value, flag

    if kind == "choice":
        options = indicator_def["options"]
        value = str(raw_value).strip()
        if value not in options:
            raise ValueError(f"'{indicator_def['name']}' uchun ro'yxatdagi qiymatlardan biri tanlanishi kerak.")
        return value, "neutral"

    # "text"
    value = str(raw_value).strip()[:300]
    if not value:
        value = "—"
    return value, "neutral"


def build_result_payload(template_key: str, submitted: Dict[str, str], note: str = "") -> Dict[str, Any]:
    """Foydalanuvchi yuborgan {indicator_key: value} lug'atini shablon
    ta'rifi bo'yicha VALIDATSIYA qiladi va DB'ga yoziladigan yakuniy
    natija lug'atini quradi. Bayroqlar (flag) HAR DOIM serverda, shablon
    ta'rifidan qayta hisoblanadi — mijoz (brauzer) tomonidan yuborilgan
    hech qanday bayroqqa ishonilmaydi.

    Xato bo'lsa ValueError ko'taradi (chaqiruvchi tomon buni foydalanuvchiga
    tushunarli xabar sifatida ko'rsatadi — 500 emas).
    """
    template = get_template(template_key)
    if not template:
        raise ValueError("Noma'lum tahlil turi tanlandi.")

    indicators_out = []
    for ind in template["indicators"]:
        key = ind["key"]
        # Yetishmayotgan/bo'sh maydon — shablonning standart (me'yoriy)
        # qiymati bilan to'ldiriladi, xatolik chiqarilmaydi (JS nosozligi
        # yoki eskirgan forma bo'lsa ham natija saqlanib qoladi).
        raw = submitted.get(key)
        if raw is None or str(raw).strip() == "":
            raw = ind["default"]
        value, flag = _flag_for(ind, raw)
        indicators_out.append({
            "key": key, "name": ind["name"], "type": ind["type"],
            "unit": ind.get("unit"), "ref_range": ind.get("ref_range"),
            "value": value, "flag": flag,
        })

    abnormal_count = sum(1 for i in indicators_out if i["flag"] in ("past", "yuqori", "ogohlantirish"))

    return {
        "template_key": template_key,
        "template_name": template["name"],
        "indicators": indicators_out,
        "note": (note or "").strip()[:1000],
        "abnormal_count": abnormal_count,
    }
