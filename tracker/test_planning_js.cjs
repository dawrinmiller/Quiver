// Run with Node's built-in test runner without installing a browser framework.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Provide the small set of page operations used by the board script.
function element(values = {}) {
    return {
        dataset: {},
        listeners: {},
        attributes: {},
        // Stand in for the page's visual drag markers.
        classList: {
            add() {
            },
            remove() {
            }
        },
        // Remember each handler so the tests can trigger it.
        addEventListener(name, callback) {
            this.listeners[name] = callback;
        },
        // Store attributes such as the card's busy state.
        setAttribute(name, value) {
            this.attributes[name] = value;
        },
        // Clear an attribute when the save finishes.
        removeAttribute(name) {
            delete this.attributes[name];
        },
        // Record when a control receives keyboard focus.
        focus() {
            this.focused = true;
        },
        ...values
    };
}

// Build a sample board with two applications and real status names.
function sampleBoard(fetchResponse) {
    const columns = ['preparing', 'applied', 'online_interview', 'offer'].map(status => {
        const column = element({
            dataset: {
                column: status
            },
            cards: [],
            count: element(),
            empty: element()
        });
        column.querySelectorAll = () => column.cards;
        // Return the counter, empty message, or card list requested by the script.
        column.querySelector = selector => {
            if (selector === '[data-column-count]') {
                return column.count;
            }
            if (selector === '[data-column-empty]') {
                return column.empty;
            }
            return {
                appendChild(card) {
                    // Move a sample card between the column lists.
                    card.column.cards = card.column.cards.filter(item => item !== card);
                    column.cards.push(card);
                    card.column = column;
                }
            };
        };
        column.contains = () => false;
        return column;
    });

    // Each sample form exposes the same named controls as the rendered template.
    function card(company, role, status) {
        const controls = [element(), element(), element(), element()];
        controls.status = controls[0];
        controls.status.value = status;
        controls.date_submitted = controls[1];
        controls.date_submitted.value = status === 'preparing' ? '' : '2026-01-15';
        controls.csrfmiddlewaretoken = controls[2];
        controls.csrfmiddlewaretoken.value = 'csrf-test-token';
        const form = element({
            elements: controls,
            action: '/applications/1/status/'
        });
        const result = element({
            dataset: {
                company,
                role,
                status
            },
            form,
            statusLabel: element(),
            dateLabel: element()
        });
        // Return the form or the labels that display its saved values.
        result.querySelector = selector => {
            if (selector === '[data-status-form]') {
                return form;
            }
            return (
                selector === '[data-status-label]' ? result.statusLabel : result.dateLabel
            );
        };
        result.column = columns.find(column => column.dataset.column === status);
        result.column.cards.push(result);
        return result;
    }
    const draft = card('Example', 'Developer', 'preparing');
    const submitted = card('Other', 'Analyst', 'applied');
    const search = element({ value: '' });
    const reset = element();
    const message = element();
    const noMatches = element();
    const board = element();
    board.querySelectorAll = selector => (
        selector === '[data-column]' ? columns : [draft, submitted]
    );
    const page = {
        'application-board': board,
        'board-search': search,
        'board-reset': reset,
        'board-message': message,
        'board-no-matches': noMatches
    };
    const calls = [];

    // Record request bodies without depending on a browser's FormData implementation.
    class TestFormData {
        // Copy the form values before the controls are disabled.
        constructor(form) {
            this.values = new Map([
                ['status', form.elements.status.value],
                ['date_submitted', form.elements.date_submitted.value]
            ]);
        }
        // Replace a value when a card is dropped into a different status.
        set(key, value) {
            this.values.set(key, value);
        }
        // Read a submitted value for an assertion.
        get(key) {
            return this.values.get(key);
        }
    }
    const context = {
        document: {
            getElementById: id => page[id]
        },
        FormData: TestFormData,
        fetch: async (url, options) => {
            // Record the request before returning the prepared response.
            calls.push({
                url,
                options
            });
            return fetchResponse(url, options);
        }
    };
    vm.createContext(context);
    vm.runInContext(
        fs.readFileSync(
            path.join(__dirname, 'static/tracker/js/application_board.js'),
            'utf8'
        ),
        context
    );
    return {
        context,
        columns,
        draft,
        submitted,
        search,
        reset,
        message,
        noMatches,
        calls
    };
}

// Search both company and title, update counts, and reset without a request.
test('board search filters company and role and updates column counts', () => {
    const page = sampleBoard(() => {});
    page.search.value = 'DEVELOPER';
    page.search.listeners.input();
    assert.equal(page.draft.hidden, false);
    assert.equal(page.submitted.hidden, true);
    assert.equal(page.columns[0].count.textContent, 1);
    assert.equal(page.columns[1].count.textContent, 0);
    assert.equal(page.columns[1].empty.hidden, false);
    page.search.value = 'other';
    page.search.listeners.input();
    assert.equal(page.submitted.hidden, false);
    page.search.value = 'no matches';
    page.search.listeners.input();
    assert.equal(page.noMatches.hidden, false);
    page.reset.listeners.click();
    assert.equal(page.draft.hidden, false);
    assert.equal(page.submitted.hidden, false);
    assert.equal(page.search.focused, true);
    assert.equal(page.calls.length, 0);
});

// Confirm the shared save function sends CSRF and moves the card after success.
test('a confirmed update moves the card and updates its labels and counts', async () => {
    const page = sampleBoard(async () => ({
        ok: true,
        json: async () => ({
            success: true,
            status: 'offer',
            label: 'Offer',
            date_submitted: '2026-01-15'
        })
    }));
    await page.context.updateBoardStatus(page.submitted, 'offer');
    assert.equal(page.calls[0].options.method, 'POST');
    assert.equal(page.calls[0].options.headers['X-CSRFToken'], 'csrf-test-token');
    assert.equal(page.calls[0].options.body.get('status'), 'offer');
    assert.equal(page.submitted.column.dataset.column, 'offer');
    assert.equal(page.submitted.dataset.status, 'offer');
    assert.equal(page.submitted.form.elements.status.value, 'offer');
    assert.equal(page.submitted.statusLabel.textContent, 'Offer');
    assert.equal(page.columns[1].count.textContent, 0);
    assert.equal(page.columns[3].count.textContent, 1);
    assert.equal(page.submitted.form.elements.status.disabled, false);
});

// Keep the card and counts unchanged when Django rejects a missing submission date.
test('a rejected update restores the status selector without moving the card', async () => {
    const page = sampleBoard(async () => ({
        ok: false,
        json: async () => ({
            success: false,
            errors: {
                date_submitted: [
                    {
                        message: 'Enter the actual submission date.'
                    }
                ]
            }
        })
    }));
    page.draft.form.elements.status.value = 'applied';
    await page.context.updateBoardStatus(page.draft, 'applied');
    assert.equal(page.draft.column.dataset.column, 'preparing');
    assert.equal(page.draft.form.elements.status.value, 'preparing');
    assert.equal(page.columns[0].count.textContent, 1);
    assert.equal(page.columns[1].count.textContent, 1);
    assert.match(page.message.textContent, /actual submission date/);
    assert.equal(page.draft.dataset.saving, undefined);
});

// Keep one outstanding request per card and show a loading message.
test('repeated drops cannot send duplicate requests while saving', async () => {
    let finish;
    const page = sampleBoard(() => new Promise(resolve => {
        finish = resolve;
    }));
    const saving = page.context.updateBoardStatus(page.submitted, 'offer');
    await page.context.updateBoardStatus(page.submitted, 'offer');
    assert.equal(page.calls.length, 1);
    assert.match(page.message.textContent, /Saving/);
    assert.equal(page.submitted.column.dataset.column, 'applied');
    finish({
        ok: true,
        json: async () => ({
            success: true,
            status: 'offer',
            label: 'Offer',
            date_submitted: '2026-01-15'
        })
    });
    await saving;
    assert.equal(page.submitted.column.dataset.column, 'offer');
});

// Give a clear recovery message when the network fails or the session expires.
test('request failures leave the card in place and restore the controls', async () => {
    for (const response of [
        async () => {
            throw new Error('Network unavailable');
        },
        async () => ({
            redirected: true
        })
    ]) {
        const page = sampleBoard(response);
        await page.context.updateBoardStatus(page.submitted, 'offer');
        assert.equal(page.submitted.column.dataset.column, 'applied');
        assert.equal(page.submitted.form.elements.status.disabled, false);
        assert.ok(page.message.textContent.length > 0);
    }
});

// Check that both a keyboard form submission and a native drop invoke the save path.
test('the status form and drag-and-drop both save through the shared handler', async () => {
    const page = sampleBoard(async () => ({
        ok: true,
        json: async () => ({
            success: true,
            status: 'offer',
            label: 'Offer',
            date_submitted: '2026-01-15'
        })
    }));
    let prevented = false;
    page.submitted.form.elements.status.value = 'offer';
    page.submitted.form.listeners.submit({
        preventDefault() {
            prevented = true;
        }
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(prevented, true);
    assert.equal(page.submitted.dataset.status, 'offer');

    page.draft.listeners.dragstart({
        target: {
            closest: () => null
        },
        dataTransfer: {
            setData() {
            }
        }
    });
    page.columns[3].listeners.drop({
        preventDefault() {
        }
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(page.calls.length, 2);
    assert.equal(page.draft.dataset.status, 'offer');
});
