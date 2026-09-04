// static/js/password-toggle.js
// ═══════════════════════════════════════════════════════════════════
// Prompt 22: Parol ko'rsatish/yashirish (eye toggle) komponenti.
//
// Nima uchun alohida fayl?
//   type="password" inputlar butun loyihada bir necha joyda uchraydi
//   (login.html, base.html'dagi parol o'zgartirish modali, kelajakda
//   qo'shiladigan boshqa formalar). Har biriga qo'lda SVG ikonka va
//   toggle mantig'ini yozish o'rniga, shu fayl har qanday
//   type="password" inputni topib avtomatik "ko'z" tugmasi bilan
//   o'raydi — HTML tomonida hech narsa o'zgartirish shart emas.
//
// Ishlatilishi:
//   1) Sahifaga shu skriptni ulang: <script src="/static/js/password-toggle.js"></script>
//   2) DOMContentLoaded'da barcha type="password" inputlar avtomatik
//      o'raladi (PasswordToggle.init()).
//   3) Keyinchalik dinamik qo'shilgan input (masalan, modal ochilganda
//      yaratilgan) uchun: PasswordToggle.attach(inputElement);
// ═══════════════════════════════════════════════════════════════════

var PasswordToggle = (function () {

    var EYE_OPEN =
        '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
        '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>' +
        '<circle cx="12" cy="12" r="3"></circle>' +
        '</svg>';

    var EYE_CLOSED =
        '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
        '<path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-7 0-11-8-11-8a21.62 21.62 0 0 1 5.06-6.06"></path>' +
        '<path d="M9.9 4.24A10.94 10.94 0 0 1 12 4c7 0 11 8 11 8a21.6 21.6 0 0 1-2.16 3.19"></path>' +
        '<path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"></path>' +
        '<line x1="1" y1="1" x2="23" y2="23"></line>' +
        '</svg>';

    // Bitta inputni "ko'z" tugmasi bilan o'raydi. Agar input allaqachon
    // o'ralgan bo'lsa (masalan, sahifa qayta ishga tushirilsa), qayta
    // ishlanmaydi.
    function attach(input) {
        if (!input || input.dataset.pwToggleAttached === 'true') return;
        if (input.tagName !== 'INPUT') return;

        var wrapper = document.createElement('div');
        wrapper.className = 'pw-toggle-wrapper';

        // Input o'z joyidan olinib, wrapper ichiga qo'yiladi — shunda
        // atrofdagi form-group joylashuvi buzilmaydi.
        input.parentNode.insertBefore(wrapper, input);
        wrapper.appendChild(input);

        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'pw-toggle-btn';
        btn.setAttribute('aria-label', "Parolni ko'rsatish");
        btn.setAttribute('aria-pressed', 'false');
        btn.tabIndex = -1; // Tab bilan o'tishda formani chalg'itmasin
        btn.innerHTML = EYE_CLOSED;
        wrapper.appendChild(btn);

        btn.addEventListener('click', function () {
            var showing = input.type === 'text';
            input.type = showing ? 'password' : 'text';
            btn.innerHTML = showing ? EYE_CLOSED : EYE_OPEN;
            btn.setAttribute('aria-pressed', showing ? 'false' : 'true');
            btn.setAttribute('aria-label', showing ? "Parolni ko'rsatish" : "Parolni yashirish");
            // Ko'z bosilgach fokus inputga qaytadi, foydalanuvchi yozishni
            // davom ettira oladi.
            input.focus();
        });

        input.dataset.pwToggleAttached = 'true';
        injectStylesOnce();
    }

    // Sahifadagi barcha type="password" inputlarni topib o'raydi.
    function init(root) {
        var scope = root || document;
        var inputs = scope.querySelectorAll('input[type="password"]');
        inputs.forEach(attach);
    }

    var stylesInjected = false;
    function injectStylesOnce() {
        if (stylesInjected) return;
        stylesInjected = true;
        var style = document.createElement('style');
        style.textContent =
            '.pw-toggle-wrapper { position: relative; display: flex; align-items: stretch; ' +
            '  flex: 1 1 auto; min-width: 0; }' +
            '.pw-toggle-wrapper input[type="password"], .pw-toggle-wrapper input[type="text"] { ' +
            '  flex: 1; min-width: 0; padding-right: 42px !important; }' +
            '.pw-toggle-btn { ' +
            '  position: absolute; right: 6px; top: 50%; transform: translateY(-50%);' +
            '  display: flex; align-items: center; justify-content: center;' +
            '  width: 30px; height: 30px; border: none; background: transparent; cursor: pointer;' +
            '  color: var(--text-muted, #7c8aa8); border-radius: 4px; padding: 0;' +
            '  transition: color 0.15s, background 0.15s; }' +
            '.pw-toggle-btn:hover { color: var(--accent-color, var(--primary, #00e5ff)); background: rgba(0,229,255,0.08); }' +
            '.pw-toggle-btn:focus-visible { outline: 2px solid var(--accent-color, var(--primary, #00e5ff)); outline-offset: 1px; }';
        document.head.appendChild(style);
    }

    document.addEventListener('DOMContentLoaded', function () {
        init(document);
    });

    return { init: init, attach: attach };
})();
