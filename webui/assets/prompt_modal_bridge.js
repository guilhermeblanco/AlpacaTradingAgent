/*
 * Bridge: "show me the prompt" from inside a report iframe.
 *
 * The researcher and risk transcripts are rendered into srcDoc iframes, so
 * their buttons cannot reach a Dash callback directly. They postMessage
 * instead, and this listener clicks the matching hidden Dash button.
 *
 * This lived in an html.Script inside the Dash layout, where it never ran:
 * React inserts elements rather than parsing HTML, and a <script> node
 * created that way is not executed. So the prompt buttons in the debate
 * transcripts had been inert. Anything in webui/assets is served and
 * executed by Dash normally, which is where a listener like this belongs.
 */
window.addEventListener('message', function (event) {
    if (!event.data || event.data.type !== 'showPrompt') {
        return;
    }

    var reportType = event.data.reportType;
    var buttons = document.querySelectorAll('[id*="show-prompt-"]');

    for (var i = 0; i < buttons.length; i++) {
        var id = buttons[i].getAttribute('id');
        if (id && id.indexOf(reportType) !== -1) {
            buttons[i].click();
            return;
        }
    }

    // A pattern-matching id serialises its dict into the attribute, so the
    // report type can be in there rather than in a plain string id.
    for (var j = 0; j < buttons.length; j++) {
        var props = buttons[j].getAttribute('data-dash-props');
        if (props && props.indexOf(reportType) !== -1) {
            buttons[j].click();
            return;
        }
    }

    console.warn('No prompt button matched report type:', reportType);
});
