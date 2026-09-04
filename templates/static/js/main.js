// static/js/main.js
// ═══════════════════════════════════════════════════════════════════
// Prompt 15.2: sahifalararo umumiy UI holati.
//
// Nima uchun alohida fayl?
//   Avval har bir sahifa o'zining base.html'idan meros olgan inline
//   skriptlarga tayanardi. Sidebar yig'ish/yoyish holati esa HECH
//   QAYERDA saqlanmasdi — sahifa yangilanganda har doim "ochiq" holatga
//   qaytardi. Bu fayl shu holatni localStorage orqali saqlaydi va barcha
//   sahifalarda (dashboard, patients, settings, ...) bir xil ishlashini
//   ta'minlaydi.
//
// Theme (Dark/Light/Auto) mantig'i ATAYLAB bu faylga ko'chirilmadi —
// u base.html'ning <head> qismida, CSS chizilishidan OLDIN ishga
// tushishi SHART (aks holda FOUC — noto'g'ri mavzu bilan miltillash
// yuz beradi), external skript esa tarmoqdan kelguncha shu miltillashni
// oldini ololmaydi. Sidebar uchun ham xuddi shunday anti-flicker qismi
// bor, lekin u juda qisqa bo'lgani uchun base.html <head>'da inline
// qoladi (qarang: `data-sidebar` atributi); shu fayldagi SidebarController
// esa faqat TUGMA MANTIG'INI (toggle, klik ishlovchisi) beradi.
// ═══════════════════════════════════════════════════════════════════

var SidebarController = (function () {
    var STORAGE_KEY = 'sidebar-collapsed'; // localStorage: 'true' | 'false'

    function isCollapsed() {
        return localStorage.getItem(STORAGE_KEY) === 'true';
    }

    function apply(collapsed) {
        if (collapsed) {
            document.documentElement.setAttribute('data-sidebar', 'collapsed');
        } else {
            document.documentElement.removeAttribute('data-sidebar');
        }
        var btn = document.getElementById('sidebarToggleBtn');
        if (btn) {
            btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            btn.title = collapsed ? "Sidebar'ni yoyish" : "Sidebar'ni yig'ish";
        }
    }

    function setCollapsed(collapsed) {
        localStorage.setItem(STORAGE_KEY, collapsed ? 'true' : 'false');
        apply(collapsed);
    }

    function toggle() {
        setCollapsed(!isCollapsed());
    }

    function init() {
        // <head>'dagi anti-flicker skripti allaqachon `data-sidebar`
        // atributini to'g'ri o'rnatgan bo'lishi mumkin — bu yerda faqat
        // tugma holatini (title/aria) shunga moslashtiramiz, qayta
        // yozib qo'ymaymiz (localStorage bilan ziddiyat bo'lmasin).
        apply(isCollapsed());
    }

    return { init: init, toggle: toggle, setCollapsed: setCollapsed, isCollapsed: isCollapsed };
})();

function toggleSidebar() {
    SidebarController.toggle();
}

// ═══════════════════════════════════════════════════════════════════
// Prompt 20: mobil (<=768px) hamburger sidebar.
//
// Desktopdagi SidebarController (yuqorida) bilan ATAYLAB
// ARALASHTIRILMAYDI: u "yig'ilgan/yoyilgan" (collapsed) — doimiy,
// localStorage'da saqlanadigan — holatni boshqaradi. Bu yerdagi
// MobileSidebarController esa "ochiq/yopiq" (slide-in/out) — vaqtinchalik,
// HAR SAHIFA YUKLANGANDA yopiq holatdan boshlanadigan — holatni
// boshqaradi (shuning uchun localStorage'ga yozilmaydi: foydalanuvchi
// sahifani yangilaganda sidebar har safar yopiq holatda ochilishi
// kerak, ochiq holatda "qotib qolmasligi" kerak).
// ═══════════════════════════════════════════════════════════════════
var MobileSidebarController = (function () {
    var ATTR = 'data-mobile-sidebar';

    function isOpen() {
        return document.documentElement.getAttribute(ATTR) === 'open';
    }

    function open() {
        document.documentElement.setAttribute(ATTR, 'open');
        // Sidebar ochiq turganda orqa fon (asosiy kontent) skroll
        // qilinmasin — aks holda foydalanuvchi sidebar ustidan
        // "orqasidagi" sahifani ham aylantirib yuborishi mumkin.
        document.body.style.overflow = 'hidden';
    }

    function close() {
        document.documentElement.removeAttribute(ATTR);
        document.body.style.overflow = '';
    }

    function toggle() {
        if (isOpen()) { close(); } else { open(); }
    }

    return { open: open, close: close, toggle: toggle, isOpen: isOpen };
})();

function toggleMobileSidebar() {
    MobileSidebarController.toggle();
}

function closeMobileSidebar() {
    MobileSidebarController.close();
}

document.addEventListener('DOMContentLoaded', function () {
    SidebarController.init();

    // Menyudagi biror havolaga bosilganda mobil sidebar avtomatik
    // yopiladi — aks holda yangi sahifaga o'tilgach ham sidebar ochiq
    // (ekranning yarmini to'sib) qolib ketardi.
    var sidebar = document.querySelector('.sidebar');
    if (sidebar) {
        sidebar.querySelectorAll('a').forEach(function (link) {
            link.addEventListener('click', function () {
                MobileSidebarController.close();
            });
        });
    }

    // ESC tugmasi bilan ham yopish mumkin (klaviatura-do'st).
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && MobileSidebarController.isOpen()) {
            MobileSidebarController.close();
        }
    });

    // Ekran mobil o'lchamdan kattaroqqa o'zgartirilsa (masalan
    // planshet aylantirilsa yoki oyna kengaytirilsa), ochiq qolgan
    // mobil-sidebar holatini tozalaymiz — aks holda keyinroq qayta
    // mobil kenglikka qaytilganda eskirgan "ochiq" holat bilan
    // (body overflow:hidden bilan birga) qolib ketishi mumkin edi.
    window.addEventListener('resize', function () {
        if (window.innerWidth > 768 && MobileSidebarController.isOpen()) {
            MobileSidebarController.close();
        }
    });
});
