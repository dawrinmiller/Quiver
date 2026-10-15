// Run with Node's built-in test runner; no extra packages are needed.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function input(value = '') {
    return {
        value,
        listeners: {},
        addEventListener(name, callback) {
            this.listeners[name] = callback;
        }
    };
}

test('company/role search and status filters compose and clear independently', () => {
    const search = input(),
        status = input(),
        empty = { hidden: true };

    const rows = [
        {
            dataset: {
                company: 'Google',
                role: 'Data Analyst',
                status: 'online_interview'
            }
        },
        {
            dataset: {
                company: 'Example',
                role: 'Data Developer',
                status: 'applied'
            }
        },
        {
            dataset: {
                company: 'Other',
                role: 'Engineer',
                status: 'preparing'
            }
        },
    ];
    const elements = {
        'application-search': search,
        'status-filter': status,
        'no-matches': empty
    };
    vm.runInNewContext(
        fs.readFileSync(
            path.join(__dirname, 'static/tracker/js/dashboard.js'),
            'utf8'
        ),
        {
            document: {
                getElementById: id => elements[id],
                querySelectorAll: () => rows
            },
        }
    );

    const visible = () => rows.map(row => !row.hidden);

    assert.deepEqual(visible(), [true, true, true]);
    search.value = 'GOO';
    search.listeners.input();
    assert.deepEqual(visible(), [true, false, false]);
    search.value = 'data';
    search.listeners.input();
    assert.deepEqual(visible(), [true, true, false]);
    status.value = 'online_interview';
    status.listeners.change();
    assert.deepEqual(visible(), [true, false, false]);
    search.value = '';
    search.listeners.input();
    assert.equal(status.value, 'online_interview');
    assert.deepEqual(visible(), [true, false, false]);
    search.value = 'developer';
    search.listeners.input();
    assert.equal(empty.hidden, false);
    status.value = '';
    status.listeners.change();
    assert.equal(search.value, 'developer');
    assert.deepEqual(visible(), [false, true, false]);
    search.value = '';
    search.listeners.input();
    assert.deepEqual(visible(), [true, true, true]);
});

test('feedback visibility and submission date requirements follow status', () => {
    for (const text of ['', 'Saved employer feedback']) {
        const status = input('preparing'),
            wrapper = {},
            date = {};
        const feedback = {
            value: text,
            closest: () => wrapper
        };
        const elements = {
            id_status: status,
            id_rejection_feedback: feedback,
            id_date_submitted: date
        };
        vm.runInNewContext(
            fs.readFileSync(
                path.join(__dirname, 'static/tracker/js/application_form.js'),
                'utf8'
            ),
            {
                document: {
                    getElementById: id => elements[id]
                },
            }
        );

        assert.equal(date.required, false);
        assert.equal(wrapper.hidden, !text);
        status.value = 'rejected';
        status.listeners.change();
        assert.equal(wrapper.hidden, false);
        assert.equal(date.required, true);
        status.value = 'applied';
        status.listeners.change();
        assert.equal(wrapper.hidden, !text);
        assert.equal(feedback.value, text);
    }
});
