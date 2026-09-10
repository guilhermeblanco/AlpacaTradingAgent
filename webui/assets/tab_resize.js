/*
 * Re-measure figures when their tab becomes visible.
 *
 * A Bootstrap tab pane is hidden with `display: none`, not unmounted —
 * which is what keeps intervals ticking and callbacks resolving across
 * tabs. The cost is that anything measuring itself while hidden measures
 * zero, so a Plotly figure drawn on an inactive tab comes back one pixel
 * wide when you switch to it and stays that way until something resizes
 * the window.
 *
 * Plotly's `responsive: true` listens for window resize, so telling the
 * window it resized is enough, and is cheaper and more robust than
 * reaching into each graph. The frame delay lets the pane finish becoming
 * visible first; measuring in the same tick measures the old state.
 */
(function () {
    function remeasure() {
        requestAnimationFrame(function () {
            window.dispatchEvent(new Event('resize'));
        });
    }

    document.addEventListener('click', function (event) {
        var tab = event.target.closest('.stage-tabs .nav-link, [role="tab"]');
        if (tab) {
            remeasure();
        }
    });

    // A tab can also change without a click — a callback setting
    // `active_tab`, which is how the setup wizard sends you to Set up.
    var observer = new MutationObserver(function (records) {
        for (var i = 0; i < records.length; i++) {
            var target = records[i].target;
            if (target.classList && target.classList.contains('tab-pane')) {
                remeasure();
                return;
            }
        }
    });

    document.addEventListener('DOMContentLoaded', function () {
        observer.observe(document.body, {
            subtree: true,
            attributes: true,
            attributeFilter: ['class'],
        });
    });
})();
