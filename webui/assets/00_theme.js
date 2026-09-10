/*
 * Apply the remembered theme before the page paints.
 *
 * Named to sort first: Dash serves everything in assets/ alphabetically,
 * and this has to run before anything draws. Light is the default and
 * lives on `:root`, so a light-preferring browser sees nothing happen
 * here at all; a dark-preferring one would otherwise get a flash of
 * light before a callback could set the attribute, which is exactly the
 * moment a theme toggle feels broken.
 *
 * The attribute goes on documentElement rather than body because that is
 * where `:root` matches, and because it needs to be set before body
 * exists.
 */
(function () {
    var STORAGE_KEY = 'tradingagents-theme';

    function remembered() {
        try {
            return window.localStorage.getItem(STORAGE_KEY);
        } catch (error) {
            // Private browsing, or storage disabled. Not a reason to fail.
            return null;
        }
    }

    function apply(value) {
        // Two attributes, because two systems are being told. `data-theme`
        // drives our own tokens; `data-bs-theme` drives Bootstrap 5.3's
        // colour modes, which is what makes its cards, inputs and tables
        // follow along instead of staying whatever the stylesheet was
        // built for.
        document.documentElement.setAttribute('data-theme', value);
        document.documentElement.setAttribute('data-bs-theme', value);
    }

    var theme = remembered();
    apply(theme === 'dark' ? 'dark' : 'light');

    // The toggle writes through this so the choice survives a reload and
    // is applied before paint on the next one.
    window.tradingagentsSetTheme = function (next) {
        var value = next === 'dark' ? 'dark' : 'light';
        apply(value);
        try {
            window.localStorage.setItem(STORAGE_KEY, value);
        } catch (error) {
            /* nothing to do; the choice lasts this page only */
        }
        return value;
    };

    window.tradingagentsTheme = function () {
        return document.documentElement.getAttribute('data-theme') || 'light';
    };
})();
